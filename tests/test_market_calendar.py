"""Tests for the shared NYSE trading-day calendar (src/ingest/market_calendar.py).

Pure stdlib (no data stack, no network): the calendar is date arithmetic over a
hardcoded holiday set, so these run on a bare interpreter like the schema contract tests.
"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ingest import market_calendar as mc  # noqa: E402


class TestIsTradingDay(unittest.TestCase):
    def test_ordinary_weekday_is_a_session(self):
        self.assertTrue(mc.is_trading_day(dt.date(2026, 6, 3)))    # a Wednesday

    def test_weekend_is_not_a_session(self):
        self.assertFalse(mc.is_trading_day(dt.date(2026, 6, 6)))   # Saturday
        self.assertFalse(mc.is_trading_day(dt.date(2026, 6, 7)))   # Sunday

    def test_listed_holidays_are_not_sessions(self):
        self.assertFalse(mc.is_trading_day(dt.date(2025, 12, 25)))  # Christmas
        self.assertFalse(mc.is_trading_day(dt.date(2026, 7, 3)))    # observed July 4
        self.assertFalse(mc.is_trading_day(dt.date(2028, 4, 14)))   # Good Friday 2028
        self.assertFalse(mc.is_trading_day(dt.date(2028, 11, 23)))  # Thanksgiving 2028
        self.assertFalse(mc.is_trading_day(dt.date(2028, 7, 4)))    # Independence Day 2028

    def test_new_year_on_saturday_is_not_observed_2028(self):
        # Jan 1 2028 is a Saturday; the NYSE does NOT close the preceding Friday for
        # New Year's Day, so Fri 2027-12-31 stays a trading day (the 2028 quirk).
        self.assertTrue(mc.is_trading_day(dt.date(2027, 12, 31)))
        self.assertTrue(mc.is_trading_day(dt.date(2028, 1, 3)))     # first 2028 session (Mon)


class TestPriorTradingDay(unittest.TestCase):
    def test_canonical_july_6_2026_case(self):
        # The F1 motivating case: Monday 2026-07-06's prior trading day is Thursday
        # 2026-07-02 - past the weekend AND the Fri 07-03 observed July 4 holiday.
        self.assertEqual(mc.prior_trading_day(dt.date(2026, 7, 6)), dt.date(2026, 7, 2))

    def test_plain_weekend_skip(self):
        self.assertEqual(mc.prior_trading_day(dt.date(2026, 6, 8)), dt.date(2026, 6, 5))  # Mon->Fri

    def test_lag_zero_returns_same_day(self):
        self.assertEqual(mc.prior_trading_day(dt.date(2026, 7, 6), 0), dt.date(2026, 7, 6))
        self.assertEqual(mc.prior_trading_day(dt.date(2026, 7, 6), -3), dt.date(2026, 7, 6))

    def test_multi_session_lag_crosses_holiday(self):
        # Two sessions back from Mon 2026-07-06: 07-02, then 07-01.
        self.assertEqual(mc.prior_trading_day(dt.date(2026, 7, 6), 2), dt.date(2026, 7, 1))

    def test_midweek_holiday_is_skipped(self):
        # Fri 2025-12-26 back one session skips the Thu 2025-12-25 Christmas holiday.
        self.assertEqual(mc.prior_trading_day(dt.date(2025, 12, 26)), dt.date(2025, 12, 24))

    def test_2028_good_friday_is_skipped(self):
        # Mon 2028-04-17 back one session skips Good Friday 2028-04-14 and the weekend.
        self.assertEqual(mc.prior_trading_day(dt.date(2028, 4, 17)), dt.date(2028, 4, 13))


class TestCaptureReexport(unittest.TestCase):
    def test_capture_reexports_the_same_function(self):
        # capture.py's public is_trading_day must BE the shared one, so the nightly
        # trading-day guard and the adapters' oi dating can never diverge (F1).
        from src.ingest.capture import is_trading_day as capture_itd
        self.assertIs(capture_itd, mc.is_trading_day)


if __name__ == "__main__":
    unittest.main()
