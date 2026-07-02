"""Tests for the signal layer (M5) and the evaluation harness. Needs the stack.

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

from src.ingest import schema  # noqa: E402

_DATES = [pd.Timestamp(d) for d in ("2024-06-03", "2024-06-04", "2024-06-05")] if _HAVE_STACK else []


def _uts(d):
    return pd.Timestamp(d.year, d.month, d.day, 20, 0, tz="UTC")


def mini_chain(contracts, *, spot, ts):
    rows = []
    for c in contracts:
        row = {n: None for n in schema.field_names()}
        row.update({
            "symbol": "T", "quote_ts": ts, "expiration": pd.Timestamp("2024-07-19"),
            "strike": float(c["strike"]), "type": c["type"], "underlying_price": spot,
            "gamma": c.get("gamma", 0.03), "open_interest": c.get("oi", 1000),
            "iv": 0.2, "_adapter": "t",
        })
        rows.append(row)
    df = pd.DataFrame(rows, columns=schema.field_names())
    df["quote_ts"] = pd.to_datetime(df["quote_ts"], utc=True)
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["oi_asof_date"] = pd.to_datetime(df["oi_asof_date"])
    scalar = {k: v for k, v in schema.pandas_dtypes().items()
              if k not in ("quote_ts", "expiration", "oi_asof_date")}
    return df.astype(scalar)


def _chains():
    d0, d1, d2 = _DATES
    return {
        d0: mini_chain([{"type": "call", "strike": 100}, {"type": "call", "strike": 110}],
                       spot=100.0, ts=_uts(d0)),                       # all calls -> +GEX
        d1: mini_chain([{"type": "put", "strike": 100}, {"type": "put", "strike": 90}],
                       spot=100.0, ts=_uts(d1)),                        # all puts -> -GEX
        d2: mini_chain([{"type": "call", "strike": 100}, {"type": "put", "strike": 100}],
                       spot=100.0, ts=_uts(d2)),                        # balanced -> flat
    }


def _bars():
    return pd.DataFrame({"open": [100.0, 100.0, 110.0], "close": [100.0, 110.0, 105.0]},
                        index=_DATES)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestSignals(unittest.TestCase):
    def test_regime_series(self):
        from src.signals import regime_series
        rs = regime_series(_chains())
        self.assertEqual([rs[d] for d in _DATES], ["+GEX", "-GEX", "flat"])

    def test_regime_signal_long_flat(self):
        from src.signals import regime_signal
        sig = regime_signal(_chains(), long=1.0, short=0.0)
        self.assertEqual([sig[d] for d in _DATES], [1.0, 0.0, 0.0])

    def test_regime_signal_long_short(self):
        from src.signals import regime_signal
        sig = regime_signal(_chains(), long=1.0, short=-1.0, flat=0.0)
        self.assertEqual([sig[d] for d in _DATES], [1.0, -1.0, 0.0])

    def test_chain_metric_series(self):
        from src.metrics import net_gex
        from src.signals import chain_metric_series
        s = chain_metric_series(_chains(), lambda df: net_gex(df))
        self.assertGreater(s[_DATES[0]], 0)   # +GEX
        self.assertLess(s[_DATES[1]], 0)      # -GEX


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestBaselines(unittest.TestCase):
    def test_random_entry_reproducible(self):
        from src.eval import random_entry_control
        a = random_entry_control(_bars(), seed=42)
        b = random_entry_control(_bars(), seed=42)
        self.assertTrue(a.equals(b))
        self.assertTrue(set(a.unique()).issubset({0.0, 1.0}))


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestAttribution(unittest.TestCase):
    def test_regime_attribution_splits_overnight_gap(self):
        # F9: the overnight gap must be booked to the regime that HELD the position
        # across it, not to the current bar's regime. Long decided at d0 (+GEX) is
        # held through d1's session AND the overnight into d2; at d2's open the
        # -GEX-driven exit fills. So the -1.82% overnight gap into d2 belongs to
        # +GEX, and -GEX (flat that session) earns ~0 - not the -1.82%.
        from src.eval import regime_attribution
        bars = pd.DataFrame({"open": [100.0, 105.0, 108.0], "close": [100.0, 110.0, 108.0]},
                            index=_DATES)
        target = pd.Series({_DATES[0]: 1.0, _DATES[1]: 0.0, _DATES[2]: 0.0})
        regimes = pd.Series({_DATES[0]: "+GEX", _DATES[1]: "-GEX", _DATES[2]: "flat"})
        attr = regime_attribution(bars, target, regimes)
        # +GEX: intraday d1 (110/105-1) + overnight into d2 (108/110-1).
        self.assertAlmostEqual(attr["+GEX"]["pnl_contribution"], (110/105 - 1) + (108/110 - 1))
        self.assertEqual(attr["+GEX"]["n_periods"], 2)
        # -GEX drove only the flat d2 session -> ~0, NOT the overnight loss.
        self.assertAlmostEqual(attr["-GEX"]["pnl_contribution"], 0.0)
        self.assertEqual(attr["flat"]["n_periods"], 0)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestCostSweep(unittest.TestCase):
    def test_sweep_grid_and_monotonic(self):
        from src.eval import cost_sweep
        target = pd.Series({_DATES[0]: 1.0, _DATES[1]: 0.0})
        sweep = cost_sweep(_bars(), target, commissions=[0.0], slippages_bps=[0.0, 10.0])
        self.assertEqual(len(sweep), 2)
        zero = sweep[sweep["slippage_bps"] == 0.0]["total_return"].iloc[0]
        ten = sweep[sweep["slippage_bps"] == 10.0]["total_return"].iloc[0]
        self.assertAlmostEqual(zero, 0.10)
        self.assertLess(ten, zero)   # more slippage -> lower net return


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestScorecard(unittest.TestCase):
    def test_scorecard_shape_and_timing_skill(self):
        from src.eval import scorecard
        from src.signals import regime_series, regime_signal
        # regime signal is partial-exposure (long only into the +GEX up-bar).
        card = scorecard(_bars(), regime_signal(_chains(), long=1.0, short=0.0),
                         regimes=regime_series(_chains()),
                         n_permutations=99, n_controls=50, bootstrap_n=100)
        self.assertEqual(len(card["config_hash"]), 16)
        for key in ("strategy", "strategy_sharpe", "strategy_mean_bar_return",
                    "bootstrap_mean_ci_95", "buy_and_hold_return", "excess_vs_buy_and_hold",
                    "permutation_test", "random_control", "regime_attribution"):
            self.assertIn(key, card)
        self.assertNotIn("beats_random_entry", card)   # no naked booleans
        self.assertGreater(card["excess_vs_buy_and_hold"], 0.0)
        # Well-timed: beats most shuffles of its own weights (sign-safe timing test).
        self.assertGreater(card["permutation_test"]["strategy_percentile"], 0.5)

    def test_permutation_denies_short_beta(self):
        # F3-proper: an always-SHORT signal on a falling market is pure (negative)
        # beta, not timing. Permuting a constant vector yields itself, so every
        # permutation ties and the percentile is exactly 0 - the sign hole the
        # long-only control missed.
        from src.eval import scorecard
        down = pd.DataFrame({"open": [100.0, 95.0, 90.0], "close": [95.0, 90.0, 85.0]},
                            index=_DATES)
        always_short = pd.Series({d: -1.0 for d in _DATES})
        card = scorecard(down, always_short, n_permutations=50, n_controls=0, bootstrap_n=50)
        self.assertEqual(card["permutation_test"]["strategy_percentile"], 0.0)

    def test_permutation_gross_basis_removes_cost_asymmetry(self):
        # F3 follow-up (fable): a permutation does NOT preserve turnover, so on a
        # market with identical per-bar moves (zero timing info) a low-turnover
        # blocky signal must NOT score as skill. The old NET test gave 1.0 here via
        # cost drag on the higher-turnover shuffles; the GROSS test does not.
        from src.eval import scorecard
        n = 20
        idx = pd.date_range("2024-06-03", periods=n, freq="B")
        opens, closes = [100.0], []
        for _ in range(n):                      # every bar +1% intrabar, no gaps
            c = opens[-1] * 1.01
            closes.append(c)
            opens.append(c)
        bars = pd.DataFrame({"open": opens[:n], "close": closes}, index=idx)
        blocky = pd.Series([1.0] * (n // 2) + [0.0] * (n // 2), index=idx)  # low turnover
        card = scorecard(bars, blocky, n_permutations=200, n_controls=0, bootstrap_n=0)
        # Correct no-skill answer is ~0.5 (centered), NOT ~1.0 as the old net test gave.
        self.assertLess(card["permutation_test"]["strategy_percentile"], 0.75)
        self.assertGreater(card["permutation_test"]["strategy_percentile"], 0.25)

    def test_exposure_matched_control_denies_free_beta(self):
        # An informationless always-long signal must NOT beat the exposure-matched
        # long-only control on a drifting-up market (both ~always in).
        from src.eval import scorecard
        up = pd.DataFrame({"open": [100.0, 105.0, 110.0], "close": [105.0, 110.0, 115.0]},
                          index=_DATES)
        always_long = pd.Series({d: 1.0 for d in _DATES})
        card = scorecard(up, always_long, n_permutations=0, n_controls=50, bootstrap_n=50)
        self.assertAlmostEqual(card["random_control"]["exposure_matched_prob"], 1.0)
        self.assertLessEqual(card["random_control"]["strategy_percentile"], 0.5)


if __name__ == "__main__":
    unittest.main()
