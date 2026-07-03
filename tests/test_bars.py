"""Tests for daily-bars shaping (src/backtest/bars.py). Needs the data stack.

Live HTTP (fetch_daily_bars) is integration-tested manually with a key; the pure
`bars_from_aggregates` shaping is tested here against synthetic Polygon results.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pandas as pd  # noqa: F401
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False

# 2024-01-03 and 2024-01-04 UTC midnight, in ms.
_T1, _T2 = 1704240000000, 1704326400000


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestBarsFromAggregates(unittest.TestCase):
    def test_shape_is_valid_bars(self):
        from src.backtest.bars import bars_from_aggregates
        from src.backtest.engine import validate_bars

        df = bars_from_aggregates([
            {"t": _T1, "o": 470.0, "h": 472.0, "l": 469.0, "c": 471.5, "v": 1e8},
            {"t": _T2, "o": 471.0, "h": 473.0, "l": 470.0, "c": 472.5, "v": 9e7},
        ])
        validate_bars(df)  # must not raise
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "volume"])
        self.assertEqual(len(df), 2)
        self.assertTrue(df.index.is_monotonic_increasing and df.index.is_unique)

    def test_sorts_and_dedups_last_wins(self):
        from src.backtest.bars import bars_from_aggregates

        df = bars_from_aggregates([
            {"t": _T2, "o": 471.0, "h": 473.0, "l": 470.0, "c": 472.5, "v": 1},
            {"t": _T1, "o": 470.0, "h": 472.0, "l": 469.0, "c": 471.5, "v": 1},
            {"t": _T2, "o": 471.0, "h": 473.0, "l": 470.0, "c": 999.0, "v": 1},  # dup ts
        ])
        self.assertEqual(len(df), 2)
        self.assertTrue(df.index.is_monotonic_increasing)
        self.assertEqual(float(df["close"].iloc[-1]), 999.0)  # keep=last

    def test_empty_raises(self):
        from src.backtest.bars import bars_from_aggregates
        with self.assertRaises(ValueError):
            bars_from_aggregates([])


if __name__ == "__main__":
    unittest.main()
