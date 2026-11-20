import unittest
from datetime import date, datetime
from decimal import Decimal

from hkex_calendar import HK, expected_date, is_session
from monitor import positions, reconcile

DAY = date(2026, 11, 18)
INDEX = Decimal("23294.75")
EXPIRY = date(2026, 11, 20)
CONTRACT = ("HSI", EXPIRY, Decimal("23900"), "short_call")


def disclosure(strike, notional):
    return [f"Short  HSI WEEKLY OPTI {strike:,} Call Option", str(-notional),
            "-1%", str(strike), "2.6%", "11/20/2026", "2", "23,295"]


class HoldingsTest(unittest.TestCase):
    def test_weekend_and_holiday_publication_grace(self):
        self.assertEqual(expected_date(datetime(2026, 11, 21, 21, tzinfo=HK)), date(2026, 11, 19))
        self.assertEqual(expected_date(datetime(2026, 11, 23, 9, tzinfo=HK)), date(2026, 11, 19))
        self.assertEqual(expected_date(datetime(2026, 11, 23, 18, tzinfo=HK)), date(2026, 11, 20))
        self.assertFalse(is_session(date(2026, 4, 7)))
        self.assertEqual(expected_date(datetime(2026, 4, 8, 18, tzinfo=HK)), date(2026, 4, 2))

    def test_swapped_option_notionals_fail_even_when_total_matches(self):
        other = ("HSI", EXPIRY, Decimal("24000"), "short_call")
        one = INDEX * 50
        holdings = {CONTRACT: 1, other: 2}
        self.assertEqual(reconcile(holdings, [disclosure(23900, one), disclosure(24000, 2 * one)], INDEX, DAY), 3 * one)
        with self.assertRaisesRegex(ValueError, "Option notional mismatch"):
            reconcile(holdings, [disclosure(23900, 2 * one), disclosure(24000, one)], INDEX, DAY)

    def test_index_etf_and_cash_classification(self):
        rows = [
            ["TRACKER FUND OF HONG KONG", "2800 HK", "", "20", "100", "2000", ""],
            ["GLOBAL X USD MM ETF", "3137 HK", "", "100", "10", "1000", ""],
            ["HSBC HOLDINGS PLC", "5 HK", "", "100", "1", "100", ""],
        ]
        totals, _ = positions(rows, DAY)
        self.assertEqual(totals["index_etf"], 2000)
        self.assertEqual(totals["cash"], 1000)
        self.assertEqual(totals["equity"], 100)
        with self.assertRaisesRegex(ValueError, "Unclassified fund"):
            positions([["UNKNOWN ETF", "2999 HK", "", "100", "1", "100", ""]], DAY)


if __name__ == "__main__":
    unittest.main()
