"""Golden tests for the expiration-calendar features (expiry.py, Batch B).

Third-Friday date math (month boundary, year wrap, exactly-on-opex) and the
fraction-of-OI-expiring-within-N-days gauge. Needs the data stack for the OI test.

    .venv/bin/python -m unittest discover -s tests -v
"""

import datetime as dt
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pandas as pd
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False

from src.ingest import schema  # noqa: E402
from src.metrics.expiry import days_to_monthly_opex, third_friday  # noqa: E402

_QD = dt.date(2024, 6, 3)


def mini_chain(contracts, *, spot=100.0, quote_date=_QD):
    qts = pd.Timestamp(quote_date.year, quote_date.month, quote_date.day, 20, 0, tz="UTC")
    rows = []
    for c in contracts:
        row = {name: None for name in schema.field_names()}
        row.update({
            "symbol": "TEST", "root": "TEST", "quote_ts": qts,
            "expiration": pd.Timestamp(c["expiration"]),
            "strike": float(c["strike"]), "type": c["type"],
            "underlying_price": float(spot),
            "open_interest": c.get("open_interest"), "_adapter": "test",
        })
        rows.append(row)
    df = pd.DataFrame(rows, columns=schema.field_names())
    df["quote_ts"] = pd.to_datetime(df["quote_ts"], utc=True)
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["oi_asof_date"] = pd.to_datetime(df["oi_asof_date"])
    scalar = {k: v for k, v in schema.pandas_dtypes().items()
              if k not in ("quote_ts", "expiration", "oi_asof_date")}
    return df.astype(scalar)


class TestThirdFriday(unittest.TestCase):
    def test_known_dates(self):
        self.assertEqual(third_friday(2024, 6), dt.date(2024, 6, 21))
        self.assertEqual(third_friday(2026, 1), dt.date(2026, 1, 16))
        self.assertEqual(third_friday(2026, 7), dt.date(2026, 7, 17))
        # First day is itself a Friday (Jan 1 2027) -> 3rd Friday is the 15th.
        self.assertEqual(third_friday(2027, 1), dt.date(2027, 1, 15))
        self.assertEqual(third_friday(2027, 1).weekday(), 4)  # Friday


class TestDaysToMonthlyOpex(unittest.TestCase):
    def test_before_this_month_opex(self):
        # 2024-06-03 -> 2024-06-21 is 18 days out.
        self.assertEqual(days_to_monthly_opex(dt.date(2024, 6, 3)), 18)

    def test_exactly_on_opex_is_zero(self):
        self.assertEqual(days_to_monthly_opex(dt.date(2024, 6, 21)), 0)

    def test_day_after_opex_rolls_to_next_month(self):
        # 2024-06-22 -> next OpEx 2024-07-19 is 27 days out.
        self.assertEqual(days_to_monthly_opex(dt.date(2024, 6, 22)), 27)

    def test_year_boundary_wrap(self):
        # 2026-12-19 (after Dec 18 OpEx) -> 2027-01-15 is 27 days out.
        self.assertEqual(days_to_monthly_opex(dt.date(2026, 12, 19)), 27)

    def test_accepts_datetime(self):
        self.assertEqual(days_to_monthly_opex(dt.datetime(2024, 6, 21, 20, 0)), 0)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestOiExpiringWithin(unittest.TestCase):
    def _chain(self):
        return mini_chain([
            {"type": "call", "strike": 100, "expiration": dt.date(2024, 6, 5),  "open_interest": 1000},  # 2d
            {"type": "put",  "strike": 100, "expiration": dt.date(2024, 6, 10), "open_interest": 3000},  # 7d
            {"type": "call", "strike": 110, "expiration": dt.date(2024, 7, 19), "open_interest": 6000},  # 46d
        ])

    def test_fraction_golden(self):
        from src.metrics import oi_expiring_within
        df = self._chain()
        self.assertAlmostEqual(oi_expiring_within(df, 7), 0.4)     # 1000+3000 of 10000
        self.assertAlmostEqual(oi_expiring_within(df, 2), 0.1)     # 1000 of 10000
        self.assertAlmostEqual(oi_expiring_within(df, 1), 0.0)     # nothing <= 1 day
        self.assertAlmostEqual(oi_expiring_within(df, 100), 1.0)   # all

    def test_zero_oi_is_nan(self):
        from src.metrics import oi_expiring_within
        df = mini_chain([
            {"type": "call", "strike": 100, "expiration": dt.date(2024, 6, 5), "open_interest": None},
        ])
        self.assertTrue(math.isnan(oi_expiring_within(df, 7)))

    def test_empty_is_nan(self):
        from src.metrics import oi_expiring_within
        self.assertTrue(math.isnan(oi_expiring_within(mini_chain([]), 7)))


if __name__ == "__main__":
    unittest.main()
