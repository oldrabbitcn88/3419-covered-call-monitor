"""Export daily exposure, option structure and missing-session status."""
import argparse
import csv
import json
from datetime import date, datetime
from math import erf, log, sqrt
from pathlib import Path

from hkex_calendar import HK, expected_date, prior_session, sessions
from monitor import MULTIPLIER, number, positions, reconcile

VOL = 0.20
FIELDS = [
    "date", "status", "source_type", "source_url", "long_exposure_hkd", "futures_weight_pct",
    "coverage_pct", "weighted_strike", "weighted_moneyness_pct", "weighted_dte",
    "expiries", "expiry_change", "delta_coverage_est_pct", "delta_vol_assumed_pct",
    "option_nominal_to_nav_pct", "option_contracts",
]


def call_delta(spot, strike, days):
    if not days:
        return 1.0 if spot > strike else (0.0 if spot < strike else 0.5)
    t = days / 365
    d1 = (log(spot / strike) + 0.5 * VOL**2 * t) / (VOL * sqrt(t))
    return (1 + erf(d1 / sqrt(2))) / 2


def report(data, through):
    with (data / "coverage.csv").open(encoding="utf-8-sig", newline="") as handle:
        series = {date.fromisoformat(row["date"]): row for row in csv.DictReader(handle)}
    observed_dates = sorted(series)
    history = []
    last = None
    last_expiries = set()
    for day in sessions(observed_dates[0], max(through, observed_dates[-1])):
        summary = series.get(day)
        if summary is None:
            history.append({"date": day, "status": "missing"})
            continue

        with (data / "holdings" / f"{day}.csv").open(encoding="utf-8-sig", newline="") as handle:
            holdings = [row for row in csv.reader(handle) if row][1:]
        details = json.loads((data / "details" / f"{day}.json").read_text(encoding="utf-8"))
        totals, legs = positions(holdings, day)
        spot = number(details["index_close"])
        official = reconcile(legs, details["option_details"], spot, day)
        long_exposure = totals["equity"] + totals["index_etf"] + totals["futures"]
        qty = sum(legs.values())
        expiries = {expiry for _, expiry, _, _ in legs}
        wstrike = sum(n * strike for (_, _, strike, _), n in legs.items()) / qty
        wdte = sum(n * (expiry - day).days for (_, expiry, _, _), n in legs.items()) / qty
        adjusted = sum(
            float(n * spot * MULTIPLIER) * call_delta(float(spot), float(strike), (expiry - day).days)
            for (_, expiry, strike, _), n in legs.items()
        )
        if official != number(summary["short_call_notional_hkd"]):
            raise ValueError(f"{day}: analysis disagrees with canonical option notional")
        displayed_nav_pct = sum(abs(number(row[2].removesuffix("%"))) for row in details["option_details"])
        history.append({
            "date": day, "status": "observed", "source_type": "issuer_daily", "source_url": summary["source"],
            "long_exposure_hkd": long_exposure, "futures_weight_pct": round(totals["futures"] / long_exposure * 100, 4),
            "coverage_pct": summary["coverage_pct"], "weighted_strike": round(wstrike, 4),
            "weighted_moneyness_pct": round((wstrike / spot - 1) * 100, 4), "weighted_dte": round(wdte, 4),
            "expiries": "|".join(str(expiry) for expiry in sorted(expiries)),
            "expiry_change": ("yes" if expiries != last_expiries else "no") if last == prior_session(day) else "",
            "delta_coverage_est_pct": round(adjusted / float(long_exposure) * 100, 4),
            "delta_vol_assumed_pct": f"{VOL * 100:g}", "option_nominal_to_nav_pct": displayed_nav_pct,
            "option_contracts": qty,
        })
        last, last_expiries = day, expiries

    with (data / "analysis.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(history)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--through", type=date.fromisoformat)
    args = parser.parse_args()
    report(args.data, args.through or expected_date(datetime.now(HK)))
