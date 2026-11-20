"""Archive Global X 3419's disclosed positions after checking both tables."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from hkex_calendar import HK, expected_date, is_session

URL = "https://www.globalxetfs.com.hk/funds/covered-call-etf/"
MULTIPLIER = 50
INDEX_ETFS = {2800, 2833, 82800, 82833}
MONEY_MARKET_ETFS = {3137, 9137}
INDEX_NAME = r"(?:HSI|HANG SENG (?:INDEX|IDX))"
HOLDING_CALL = re.compile(
    rf"CALL {INDEX_NAME}(?: WEEKLY)?(?: OPT(?:ION)?S?)? (\d{{2}}/\d{{2}}/\d{{2}}) ([\d,]+)", re.I
)
OFFICIAL_CALL = re.compile(
    rf"SHORT {INDEX_NAME}(?: WEEKLY)?(?: OPTI(?:ON)?S?)? ([\d,]+) CALL OPTION", re.I
)
FUTURE = re.compile(r"HANG SENG (?:IDX|INDEX) FUT(?:URE)?S? (\d{2}/\d{2}/\d{2})", re.I)
FIELDS = [
    "date", "equity_hkd", "futures_notional_hkd", "short_call_notional_hkd",
    "coverage_pct", "hsi_close", "option_contracts", "source", "index_etf_hkd",
]


def number(value):
    parsed = Decimal(value.replace(",", "").replace("HKD$", "").strip())
    if not parsed.is_finite():
        raise ValueError(f"Invalid numeric value: {value!r}")
    return parsed


def parse_expiry(text):
    return datetime.strptime(text, "%m/%d/%Y" if len(text) == 10 else "%m/%d/%y").date()


def holding_call(name):
    match = HOLDING_CALL.fullmatch(name)
    if not match:
        return None
    expiry, strike = match.groups()
    return ("HSI", parse_expiry(expiry), number(strike), "short_call")


def positions(rows, as_of):
    totals = dict.fromkeys(("equity", "index_etf", "cash", "futures"), Decimal(0))
    options = defaultdict(Decimal)
    for name, ticker, _, price, qty, value, _ in rows:
        equity_code = re.fullmatch(r"(\d+) HK", ticker)
        future = FUTURE.fullmatch(name)
        option = holding_call(name)
        if future:
            quantity = number(qty)
            if quantity <= 0 or quantity != quantity.to_integral_value() or number(price) <= 0:
                raise ValueError(f"Invalid long futures position: {name}")
            if parse_expiry(future[1]) < as_of or not re.fullmatch(r"(?:HI|HSI)[FGHJKMNQUVXZ]\d{1,2}(?: INDEX)?", ticker):
                raise ValueError(f"Invalid HSI futures contract: {name}, {ticker}")
            totals["futures"] += quantity * number(price) * MULTIPLIER
        elif option:
            quantity = number(qty)
            if quantity >= 0 or quantity != quantity.to_integral_value() or option[1] < as_of:
                raise ValueError(f"Invalid short call position: {name}")
            options[option] -= quantity
        elif equity_code:
            code = int(equity_code[1])
            if number(qty) < 0 or number(value) < 0:
                raise ValueError(f"Invalid long security position: {name}")
            if code in MONEY_MARKET_ETFS:
                totals["cash"] += number(value)
            elif code in INDEX_ETFS:
                totals["index_etf"] += number(value)
            elif re.search(r"\b(?:ETF|FUND|MONEY MARKET)\b", name, re.I):
                raise ValueError(f"Unclassified fund: {name}, {ticker}")
            else:
                totals["equity"] += number(value)
        elif not ticker and re.fullmatch(r"(?:CASH(?: (?:HKD|USD|AND CASH EQUIVALENTS))?|(?:HKD|USD) CASH)", name, re.I):
            totals["cash"] += number(value)
        else:
            raise ValueError(f"Unclassified position: {name}, {ticker}")
    return totals, options


def reconcile(options, option_rows, index, as_of):
    official = defaultdict(Decimal)
    for row in option_rows:
        if len(row) != 8:
            raise ValueError("Unexpected option disclosure columns")
        name, notional, _, strike, _, expiry, dte, displayed_index = row
        match = OFFICIAL_CALL.fullmatch(" ".join(name.split()))
        if not match or number(match[1]) != number(strike) or number(strike) <= 0:
            raise ValueError(f"Unclassified option disclosure or inconsistent strike: {name}")
        expiry = parse_expiry(expiry)
        if expiry < as_of or number(dte) != (expiry - as_of).days or abs(number(displayed_index) - index) > 1:
            raise ValueError(f"Invalid option disclosure date or index: {name}")
        value = -number(notional)
        if value <= 0:
            raise ValueError(f"Invalid short-call notional: {name}")
        official[("HSI", expiry, number(strike), "short_call")] += value

    if not options or options.keys() != official.keys():
        raise ValueError(f"Option contract mismatch: holdings {sorted(options)}, disclosure {sorted(official)}")
    for contract, quantity in options.items():
        value = official[contract]
        if abs(quantity * index * MULTIPLIER - value) > max(Decimal("1"), value * Decimal("0.0001")):
            raise ValueError(f"Option notional mismatch for {contract}")
    return sum(official.values())


def parse_page(html):
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    table = soup.select_one("#top-ten")
    option_heading = soup.select_one("h3#Holdings small")
    if table is None or option_heading is None:
        raise ValueError("Holdings or option disclosure heading is missing")
    heading = table.find_previous("h3")
    holdings_heading = heading.select_one("small") if heading else None
    if holdings_heading is None:
        raise ValueError("Holdings disclosure date is missing")
    as_of = datetime.strptime(holdings_heading.get_text(" ", strip=True).removeprefix("As of "), "%d %b %Y").date()
    option_as_of = datetime.strptime(option_heading.get_text(" ", strip=True).removeprefix("As of "), "%d %b %Y").date()
    if as_of != option_as_of:
        raise ValueError("Option and holdings dates differ")

    rows = [[cell.get_text(" ", strip=True) for cell in tr.select("td")] for tr in table.select("tbody tr")]
    header = [cell.get_text(" ", strip=True) for cell in table.select("thead th")]
    if len(header) != 7 or len(rows) < 20 or any(len(row) != 7 for row in rows):
        raise ValueError("Unexpected holdings table")
    facts = {}
    for tr in soup.select("tr"):
        cells = tr.select("td")
        if len(cells) == 2:
            facts[cells[0].get_text(" ", strip=True)] = cells[1].get_text(" ", strip=True)
    index = number(facts["Closing level of Hang Seng Index"])
    if index <= 0:
        raise ValueError("Invalid index close")
    tables = [t for t in soup.select("table") if "Option Position" in t.get_text(" ", strip=True)
              and "Notional Exposure" in t.get_text(" ", strip=True)]
    if len(tables) != 1:
        raise ValueError("Option details table missing or ambiguous")
    option_rows = [[c.get_text(" ", strip=True) for c in tr.select("td")]
                   for tr in tables[0].select("tr") if tr.select("td")]
    nav = facts.get("Total Net Asset Value of the Fund (in HKD)")
    nav = number(nav.removeprefix("HKD ").strip()) if nav else None
    if nav is not None and nav <= 0:
        raise ValueError("Invalid fund NAV")
    return as_of, header, rows, index, option_rows, nav


def csv_bytes(rows):
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def archive(directory, label, html, metadata):
    directory.mkdir(parents=True, exist_ok=True)
    browser_snapshot = directory / f"{label}.html.gz"
    record = directory / f"{label}.json"
    if not record.exists():
        browser_snapshot.write_bytes(gzip.compress(html, mtime=0))
        record.write_bytes(json_bytes({**metadata, "html_sha256": hashlib.sha256(html).hexdigest()}))


def save_observation(data, html, captured_at):
    as_of, header, holdings, index, option_rows, nav = parse_page(html)
    if not is_session(as_of) or as_of > captured_at.astimezone(HK).date():
        raise ValueError(f"Invalid holdings date: {as_of}")

    day = as_of.isoformat()
    metadata = {
        "date": day, "captured_at_utc": captured_at.isoformat(),
        "index_close": str(index), "option_details": option_rows, "url": URL,
    }
    if nav is not None:
        metadata["nav_total"] = str(nav)
    holdings_content = csv_bytes([header, *holdings])
    holdings_file = data / "holdings" / f"{day}.csv"
    details_file = data / "details" / f"{day}.json"
    if holdings_file.exists() or details_file.exists():
        if not (holdings_file.exists() and details_file.exists()):
            raise ValueError(f"Incomplete canonical snapshot for {day}")
        with holdings_file.open(encoding="utf-8-sig", newline="") as handle:
            old_holdings = [row for row in csv.reader(handle) if row]
        prior = json.loads(details_file.read_text(encoding="utf-8"))
        related = ("date", "index_close", "option_details")
        if "nav_total" in prior:
            related += ("nav_total",)
        if old_holdings != [header, *holdings] or any(prior[key] != metadata.get(key) for key in related):
            revision = hashlib.sha256(holdings_content + json_bytes([metadata.get(key) for key in related])).hexdigest()
            archive(data / "revisions" / day, revision, html, {**metadata, "status": "pending_review"})
            raise ValueError(f"Same-day revision for {day}: {revision}; canonical data requires review")

    totals, options = positions(holdings, as_of)
    official = reconcile(options, option_rows, index, as_of)
    long_exposure = totals["equity"] + totals["index_etf"] + totals["futures"]
    if long_exposure <= 0:
        raise ValueError("Invalid denominator")

    path = data / "coverage.csv"
    series = {}
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            series = {row["date"]: row for row in csv.DictReader(handle)}
    series[day] = dict(zip(FIELDS, [
        day, totals["equity"], totals["futures"], official,
        (official / long_exposure * 100).quantize(Decimal(".0001")),
        index, sum(options.values()), URL, totals["index_etf"],
    ]))
    if not holdings_file.exists():
        holdings_file.parent.mkdir(parents=True, exist_ok=True)
        details_file.parent.mkdir(parents=True, exist_ok=True)
        holdings_file.write_bytes(holdings_content)
        details_file.write_bytes(json_bytes(metadata))
    archive(data / "page", day, html, metadata)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(series[key] for key in sorted(series))
    path.write_bytes(buffer.getvalue().encode("utf-8-sig"))
    print(f"{day}: coverage {series[day]['coverage_pct']}%")
    return as_of


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--diagnostics", type=Path, default=Path("_diagnostics"))
    args = parser.parse_args()
    response = requests.get(URL, timeout=45, headers={"User-Agent": "Mozilla/5.0 (compatible; holdings-research/1.0)"})
    captured_at = datetime.now(timezone.utc)
    args.diagnostics.mkdir(parents=True, exist_ok=True)
    (args.diagnostics / "latest.html").write_bytes(response.content)
    response.raise_for_status()
    observed = save_observation(args.data, response.content, captured_at)
    expected = expected_date(captured_at)
    print(f"Latest disclosure: {observed}; earliest acceptable date: {expected}, Hong Kong time")
    if observed < expected:
        raise ValueError(f"Stale issuer disclosure: {observed}; expected at least {expected}")


if __name__ == "__main__":
    main()
