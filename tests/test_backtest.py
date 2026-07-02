"""Golden + no-lookahead tests for the M4 backtester. Needs the data stack.

    .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pandas as pd
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False

if _HAVE_STACK:
    from src.config import EngineConfig

_DATES = ["2024-06-03", "2024-06-04", "2024-06-05"]


def bars(opens, closes):
    idx = pd.to_datetime(_DATES[:len(opens)])
    return pd.DataFrame({"open": opens, "close": closes}, index=idx)


def target(mapping):
    """mapping: {date_str: weight} -> Series indexed by timestamp."""
    return pd.Series({pd.Timestamp(k): v for k, v in mapping.items()})


# A clean scenario: flat day, +10% day, -4.5% day.
def scenario():
    return bars([100.0, 100.0, 110.0], [100.0, 110.0, 105.0])


ZERO_COST = None  # set in setUp when stack present


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestAccountingGolden(unittest.TestCase):
    def setUp(self):
        self.zero_cost = EngineConfig.from_dict(
            {"costs": {"commission_per_trade": 0.0, "slippage_bps": 0.0}})

    def test_next_open_fill_equity_path(self):
        from src.backtest import run_backtest
        # Long formed at d0 close -> filled at d1 open (100); exit formed at d1 -> filled d2 open (110).
        res = run_backtest(scenario(), target({"2024-06-03": 1.0, "2024-06-04": 0.0}),
                           config=self.zero_cost)
        self.assertEqual(res.net_equity.tolist(), [100000.0, 110000.0, 110000.0])
        self.assertAlmostEqual(res.stats["total_return"], 0.10)
        self.assertEqual(res.stats["n_trades"], 2)
        self.assertAlmostEqual(res.stats["max_drawdown"], 0.0)

    def test_hold_when_target_missing(self):
        from src.backtest import run_backtest
        # Only d0 has a target: buy at d1 open (100), then hold through d2 (close 105).
        res = run_backtest(scenario(), target({"2024-06-03": 1.0}), config=self.zero_cost)
        self.assertEqual(res.net_equity.tolist(), [100000.0, 110000.0, 105000.0])
        self.assertEqual(res.stats["n_trades"], 1)
        self.assertAlmostEqual(res.stats["max_drawdown"], 105000.0 / 110000.0 - 1.0)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestCosts(unittest.TestCase):
    def test_costs_drag_and_gross_vs_net(self):
        from src.backtest import run_backtest
        # Default config: commission 0, slippage 1 bp. Buy 1000@100 -> 10; sell 1000@110 -> 11.
        res = run_backtest(scenario(), target({"2024-06-03": 1.0, "2024-06-04": 0.0}))
        self.assertAlmostEqual(res.stats["total_cost"], 21.0)
        self.assertAlmostEqual(res.net_equity.iloc[-1], 109979.0)
        self.assertAlmostEqual(res.gross_equity.iloc[-1], 110000.0)
        self.assertGreater(res.stats["cost_drag"], 0.0)

    def test_half_spread_bps_adds_cost(self):
        # F6: half_spread_bps is now wired; 100 bps of half-spread must cost more.
        from src.backtest import run_backtest
        sig = target({"2024-06-03": 1.0, "2024-06-04": 0.0})
        base = run_backtest(scenario(), sig, config=EngineConfig.from_dict(
            {"costs": {"commission_per_trade": 0.0, "slippage_bps": 0.0, "half_spread_bps": 0.0}}))
        spread = run_backtest(scenario(), sig, config=EngineConfig.from_dict(
            {"costs": {"commission_per_trade": 0.0, "slippage_bps": 0.0, "half_spread_bps": 100.0}}))
        self.assertAlmostEqual(base.stats["total_cost"], 0.0)
        self.assertGreater(spread.stats["total_cost"], 0.0)
        self.assertLess(spread.net_equity.iloc[-1], base.net_equity.iloc[-1])

    def test_zero_cost_net_equals_gross(self):
        from src.backtest import run_backtest
        cfg = EngineConfig.from_dict({"costs": {"commission_per_trade": 0.0, "slippage_bps": 0.0}})
        res = run_backtest(scenario(), target({"2024-06-03": 1.0, "2024-06-04": 0.0}), config=cfg)
        self.assertEqual(res.net_equity.tolist(), res.gross_equity.tolist())
        self.assertEqual(res.stats["total_cost"], 0.0)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestNoLookahead(unittest.TestCase):
    def test_fill_timing_differs_from_same_bar(self):
        from src.backtest import run_backtest
        sig = target({"2024-06-03": 1.0, "2024-06-04": 0.0})

        next_open = run_backtest(scenario(), sig, config=EngineConfig.from_dict(
            {"costs": {"commission_per_trade": 0.0, "slippage_bps": 0.0}}))
        same_bar = run_backtest(scenario(), sig, config=EngineConfig.from_dict({
            "costs": {"commission_per_trade": 0.0, "slippage_bps": 0.0},
            "backtest": {"allow_same_bar_fill": True}}))

        # The d0 signal fills one bar LATER under the point-in-time (default) engine.
        self.assertEqual(next_open.trades["ts"].iloc[0], pd.Timestamp("2024-06-04"))
        self.assertEqual(same_bar.trades["ts"].iloc[0], pd.Timestamp("2024-06-03"))


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestStatsAndBaseline(unittest.TestCase):
    def test_buy_and_hold(self):
        from src.backtest import buy_and_hold
        eq = buy_and_hold(scenario(), 100000.0)
        self.assertEqual(eq.tolist(), [100000.0, 110000.0, 105000.0])

    def test_max_drawdown(self):
        from src.backtest import max_drawdown
        eq = pd.Series([100.0, 120.0, 90.0, 110.0])
        self.assertAlmostEqual(max_drawdown(eq), 90.0 / 120.0 - 1.0)  # -0.25


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestBarsValidation(unittest.TestCase):
    def test_non_datetime_index_rejected(self):
        from src.backtest import validate_bars
        bad = pd.DataFrame({"open": [1.0], "close": [1.0]})  # RangeIndex
        with self.assertRaises(ValueError):
            validate_bars(bad)

    def test_missing_column_rejected(self):
        from src.backtest import validate_bars
        bad = pd.DataFrame({"open": [1.0]}, index=pd.to_datetime(["2024-06-03"]))
        with self.assertRaises(ValueError):
            validate_bars(bad)

    def test_non_positive_price_rejected(self):
        from src.backtest import validate_bars
        bad = pd.DataFrame({"open": [1.0], "close": [0.0]}, index=pd.to_datetime(["2024-06-03"]))
        with self.assertRaises(ValueError):
            validate_bars(bad)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestBacktestGuards(unittest.TestCase):
    def test_weight_out_of_range_rejected(self):
        # F15: a target weight outside [-1, 1] is a typo/leverage; fail loudly.
        from src.backtest import run_backtest
        with self.assertRaises(ValueError):
            run_backtest(scenario(), target({"2024-06-03": 1.5}))

    def test_nonintersecting_target_rejected(self):
        # F7: a target keyed to timestamps not in bars would silently do nothing.
        from src.backtest import run_backtest
        stray = pd.Series({pd.Timestamp("2099-01-01"): 1.0})
        with self.assertRaises(ValueError):
            run_backtest(scenario(), stray)


if __name__ == "__main__":
    unittest.main()
