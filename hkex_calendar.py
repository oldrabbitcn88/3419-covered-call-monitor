"""HKEX securities-market sessions used by the holdings monitor."""
from datetime import date, time, timedelta, timezone

HK = timezone(timedelta(hours=8))
PUBLICATION_CUTOFF = time(18)
DAY = timedelta(days=1)

# HKEX circulars CT/063/24, CT/075/25 and CT/077/26. Half-days are sessions.
HOLIDAYS = {
    2025: "01-01 01-29 01-30 01-31 04-04 04-18 04-21 05-01 05-05 07-01 10-01 10-07 10-29 12-25 12-26".split(),
    2026: "01-01 02-17 02-18 02-19 04-03 04-06 04-07 05-01 05-25 06-19 07-01 10-01 10-19 12-25".split(),
    2027: "01-01 02-08 02-09 03-26 03-29 04-05 05-13 06-09 07-01 09-16 10-01 10-08 12-27".split(),
}


def is_session(day):
    if day.year not in HOLIDAYS:
        raise ValueError(f"Update the HKEX market calendar for {day.year}")
    return day.weekday() < 5 and day.isoformat()[5:] not in HOLIDAYS[day.year]


def prior_session(day):
    day -= DAY
    while not is_session(day):
        day -= DAY
    return day


def expected_date(captured_at):
    local = captured_at.astimezone(HK)
    today = local.date()
    due_on = today if is_session(today) and local.time() >= PUBLICATION_CUTOFF else prior_session(today)
    return prior_session(due_on)


def sessions(start, end):
    for offset in range((end - start).days + 1):
        day = start + offset * DAY
        if is_session(day):
            yield day
