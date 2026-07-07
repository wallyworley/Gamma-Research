"""Tests for the volatility-forecast harness (src/eval/volatility.py, Batch C item 3).

Estimator golden tests on tiny hand-computed series, target-alignment goldens, and
seeded synthetic experiments proving the scorecard tells a planted next-day-vol
driver from pure noise and refuses to credit lookahead.

    .venv/bin/python -m unittest discover -s tests -v

The heavy imports (numpy/pandas and src.eval, which pull in the data stack) stay
behind the _HAVE_STACK guard so the stdlib-only CI leg collects this module cleanly.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import numpy as np
    import pandas as pd
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False


if _HAVE_STACK:
    def _bars_from_x(x):
        """close constant = 100, so the range measure (H-L)/prior_close equals x[d]."""
        n = len(x)
        idx = pd.date_range("2020-01-01", periods=n, freq="B")
        close = np.full(n, 100.0)
        return pd.DataFrame(
            {"open": close, "high": 100.0 + 50.0 * x, "low": 100.0 - 50.0 * x,
             "close": close, "volume": np.ones(n)}, index=idx)

    def _gen(n=320, seed=0, beta=0.002, eps=0.004, mode="planted", mu=0.02, phi=0.6):
        """AR(1) daily range with a planted signal driver. mode 'planted' => yesterday's
        signal drives today's range (a genuine next-day lead); mode 'contemp' => the
        signal drives the SAME day (used to expose lookahead). Returns (bars, signal)."""
        rng = np.random.default_rng(seed)
        signal = rng.standard_normal(n)
        noise = rng.standard_normal(n) * eps
        x = np.zeros(n)
        x[0] = mu
        for d in range(1, n):
            drive = signal[d - 1] if mode == "planted" else signal[d]
            x[d] = mu + phi * (x[d - 1] - mu) + beta * drive + noise[d]
        x = np.clip(x, 1e-3, 0.5)
        bars = _bars_from_x(x)
        return bars, pd.Series(signal, index=bars.index)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestEstimators(unittest.TestCase):
    def test_realized_vol_cc_golden(self):
        from src.eval import realized_vol_cc
        bars = pd.DataFrame(
            {"open": [1, 1, 1, 1], "high": [1, 1, 1, 1], "low": [1, 1, 1, 1],
             "close": [100.0, 101.0, 102.0, 101.0]},
            index=pd.date_range("2020-01-01", periods=4, freq="B"))
        rv = realized_vol_cc(bars, 3)
        # log returns of [100,101,102,101]; sample std (ddof=1) * sqrt(252).
        r = np.log(np.array([101 / 100, 102 / 101, 101 / 102]))
        self.assertTrue(math.isnan(rv.iloc[0]))                    # first window incomplete
        self.assertAlmostEqual(rv.iloc[-1], float(r.std(ddof=1) * np.sqrt(252)), places=9)
        self.assertAlmostEqual(rv.iloc[-1], 0.181046, places=5)    # hand-computed

    def test_realized_vol_parkinson_golden(self):
        from src.eval import realized_vol_parkinson
        bars = pd.DataFrame(
            {"open": [1, 1], "high": [110.0, 105.0], "low": [90.0, 95.0], "close": [1, 1]},
            index=pd.date_range("2020-01-01", periods=2, freq="B"))
        pk = realized_vol_parkinson(bars, 2)
        u = np.log(np.array([110 / 90, 105 / 95])) ** 2
        expected = math.sqrt((1.0 / (4.0 * math.log(2.0))) * u.mean()) * math.sqrt(252)
        self.assertAlmostEqual(pk.iloc[-1], expected, places=9)
        self.assertAlmostEqual(pk.iloc[-1], 1.511693, places=5)

    def test_parkinson_drops_invalid_hl_rows(self):
        from src.eval import realized_vol_parkinson
        # A null high in the middle row must be dropped from the window mean, so the
        # 3-window estimate equals the mean over the two VALID rows only.
        bars = pd.DataFrame(
            {"open": [1, 1, 1], "high": [110.0, None, 105.0], "low": [90.0, 95.0, 95.0],
             "close": [1, 1, 1]},
            index=pd.date_range("2020-01-01", periods=3, freq="B"))
        pk = realized_vol_parkinson(bars, 3)
        u = np.log(np.array([110 / 90, 105 / 95])) ** 2          # only the two valid rows
        expected = math.sqrt((1.0 / (4.0 * math.log(2.0))) * u.mean()) * math.sqrt(252)
        self.assertAlmostEqual(pk.iloc[-1], expected, places=9)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestTargets(unittest.TestCase):
    def _bars(self):
        return pd.DataFrame(
            {"open": [1, 1, 1], "high": [110.0, 120.0, 130.0], "low": [90.0, 80.0, 70.0],
             "close": [100.0, 110.0, 105.0]},
            index=pd.date_range("2020-01-01", periods=3, freq="B"))

    def test_next_day_range_alignment(self):
        from src.eval import next_day_range
        ndr = next_day_range(self._bars())
        # row t carries the OUTCOME of t+1: (H_{t+1}-L_{t+1})/C_t.
        self.assertAlmostEqual(ndr.iloc[0], (120 - 80) / 100.0)     # from day1
        self.assertAlmostEqual(ndr.iloc[1], (130 - 70) / 110.0)     # from day2
        self.assertTrue(math.isnan(ndr.iloc[2]))                    # no t+1 for the last row

    def test_next_day_abs_return_alignment(self):
        from src.eval import next_day_abs_return
        nda = next_day_abs_return(self._bars())
        self.assertAlmostEqual(nda.iloc[0], abs(110 / 100 - 1))
        self.assertAlmostEqual(nda.iloc[1], abs(105 / 110 - 1))
        self.assertTrue(math.isnan(nda.iloc[2]))


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestHarFeatures(unittest.TestCase):
    def test_har_columns_and_windows(self):
        from src.eval import har_features
        rv = pd.Series(np.arange(1.0, 31.0))
        har = har_features(rv)
        self.assertEqual(list(har.columns), ["RV_d", "RV_w", "RV_m"])
        self.assertTrue((har["RV_d"] == rv).all())                  # RV_d is today's
        self.assertAlmostEqual(har["RV_w"].iloc[4], rv.iloc[0:5].mean())     # trailing 5
        self.assertTrue(math.isnan(har["RV_w"].iloc[3]))            # <5 obs -> NaN
        self.assertAlmostEqual(har["RV_m"].iloc[21], rv.iloc[0:22].mean())   # trailing 22
        self.assertTrue(math.isnan(har["RV_m"].iloc[20]))


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestVolForecastScorecard(unittest.TestCase):
    def test_planted_signal_detected(self):
        # A planted next-day driver must show clearly positive incremental R2, a
        # bootstrap fraction<=0 that is small, and a CI well above 0.
        from src.eval import vol_forecast_scorecard
        bars, signal = _gen(mode="planted", seed=1)
        card = vol_forecast_scorecard(bars, signal, n_bootstrap=300, seed=0)
        self.assertEqual(len(card["config_hash"]), 16)
        self.assertGreater(card["n_obs"], 60)
        self.assertGreater(card["baseline_r2"], 0.1)               # real vol clustering exists
        self.assertGreater(card["incremental_r2"], 0.05)
        self.assertGreater(card["incremental_r2_adj"], 0.05)
        self.assertGreater(card["signal_tstat"], 3.0)
        self.assertLess(card["bootstrap"]["frac_incremental_le_0"], 0.15)
        lo, hi = card["bootstrap"]["incremental_r2_adj_ci95"]
        self.assertGreater(lo, 0.0)                                 # whole CI above 0

    def test_noise_signal_rejected(self):
        # A pure-noise signal on the SAME process must show ~0 incremental value and a
        # bootstrap CI that straddles 0 (the honest null).
        from src.eval import vol_forecast_scorecard
        bars, _ = _gen(mode="planted", seed=1)
        rng = np.random.default_rng(99)
        noise = pd.Series(rng.standard_normal(len(bars)), index=bars.index)
        card = vol_forecast_scorecard(bars, noise, n_bootstrap=300, seed=0)
        self.assertLess(abs(card["incremental_r2_adj"]), 0.02)
        self.assertLess(abs(card["signal_tstat"]), 2.5)
        lo, hi = card["bootstrap"]["incremental_r2_adj_ci95"]
        self.assertLess(lo, 0.0)                                    # CI straddles 0
        self.assertGreater(hi, 0.0)
        self.assertGreater(card["bootstrap"]["frac_incremental_le_0"], 0.2)
        self.assertLess(card["bootstrap"]["frac_incremental_le_0"], 0.8)

    def test_lookahead_shift_increases_r2(self):
        # Alignment proof: on a CONTEMPORANEOUS driver, shifting the signal one day
        # forward (peeking at the outcome day) must INCREASE the fitted R2. The honest
        # (as-of-t) version must be strictly lower - the harness does not cheat.
        from src.eval import vol_forecast_scorecard
        bars, signal = _gen(mode="contemp", seed=2)
        honest = vol_forecast_scorecard(bars, signal, n_bootstrap=0, seed=0)
        lookahead = vol_forecast_scorecard(bars, signal.shift(-1), n_bootstrap=0, seed=0)
        self.assertLess(honest["augmented_r2"], lookahead["augmented_r2"])
        self.assertLess(honest["incremental_r2"], lookahead["incremental_r2"])

    def test_insufficient_data_returns_nan(self):
        from src.eval import vol_forecast_scorecard
        bars, signal = _gen(n=30, seed=3)                          # < 60 usable rows
        card = vol_forecast_scorecard(bars, signal, n_bootstrap=10, seed=0)
        self.assertTrue(card["insufficient_data"])
        self.assertTrue(math.isnan(card["incremental_r2"]))
        self.assertEqual(len(card["config_hash"]), 16)             # still stamped

    def test_alternate_targets_run(self):
        from src.eval import vol_forecast_scorecard
        bars, signal = _gen(mode="planted", seed=1)
        for target in ("abs_return", "parkinson"):
            card = vol_forecast_scorecard(bars, signal, target=target, n_bootstrap=50, seed=0)
            self.assertEqual(card["target"], target)
            self.assertFalse(card["insufficient_data"])

    def test_sign_consistency_subsample(self):
        from src.eval import vol_forecast_scorecard
        bars, signal = _gen(mode="planted", seed=1)                # signed signal
        card = vol_forecast_scorecard(bars, signal, n_bootstrap=0, seed=0)
        sc = card["sign_consistency"]
        self.assertTrue(sc["signed"])
        self.assertIsNotNone(sc["subsample"])
        self.assertIn("positive", sc["subsample"])
        self.assertIn("negative", sc["subsample"])

    def test_unknown_target_raises(self):
        from src.eval import vol_forecast_scorecard
        bars, signal = _gen(n=80, seed=1)
        with self.assertRaises(ValueError):
            vol_forecast_scorecard(bars, signal, target="nope")


if __name__ == "__main__":
    unittest.main()
