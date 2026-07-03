"""Golden tests for the GEX metric engine (M2).

Exact hand-computed Net GEX on mini-chains, config-driven sign/form switches, an
exact Black-Scholes gamma value, and ZeroGEX validated by its defining property
(Net GEX changes sign across the returned flip). Needs the data stack.

    .venv/bin/python -m unittest discover -s tests -v
"""

import datetime as dt
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

from src.ingest import schema  # noqa: E402

_QUOTE_DATE = dt.date(2024, 6, 3)
_FAR_EXP = dt.date(2024, 7, 19)


def mini_chain(contracts, *, spot, quote_date=_QUOTE_DATE):
    """Build a valid canonical frame from partial contract dicts."""
    quote_ts = pd.Timestamp(quote_date.year, quote_date.month, quote_date.day, 20, 0, tz="UTC")
    rows = []
    for c in contracts:
        row = {name: None for name in schema.field_names()}
        row.update({
            "symbol": "TEST",
            "root": c.get("root", "TEST"),
            "quote_ts": quote_ts,
            "expiration": pd.Timestamp(c.get("expiration", _FAR_EXP)),
            "strike": float(c["strike"]),
            "type": c["type"],
            "underlying_price": float(spot),
            "gamma": c.get("gamma"),
            "open_interest": c.get("open_interest"),
            "iv": c.get("iv"),
            "_adapter": "test",
        })
        rows.append(row)
    df = pd.DataFrame(rows, columns=schema.field_names())
    df["quote_ts"] = pd.to_datetime(df["quote_ts"], utc=True)
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["oi_asof_date"] = pd.to_datetime(df["oi_asof_date"])
    scalar = {k: v for k, v in schema.pandas_dtypes().items()
              if k not in ("quote_ts", "expiration", "oi_asof_date")}
    return df.astype(scalar)


@unittest.skipUnless(_HAVE_STACK, "numpy/pandas not installed")
class TestNetGex(unittest.TestCase):
    def setUp(self):
        # spot=100 => dollar factor = 100^2 * 0.01 = 100; contract size = 100.
        # per-contract $GEX = sign * gamma * OI * 100 * 100 = sign * gamma * OI * 10000.
        self.df = mini_chain([
            {"type": "call", "strike": 100, "gamma": 0.05, "open_interest": 1000},  # +500,000
            {"type": "put",  "strike": 100, "gamma": 0.04, "open_interest": 2000},  # -800,000
            {"type": "call", "strike": 110, "gamma": 0.03, "open_interest": 500},   # +150,000
        ], spot=100.0)

    def test_frame_is_valid(self):
        self.assertEqual(schema.validate_frame(self.df), [])

    def test_contract_gex_values(self):
        from src.metrics import contract_gex
        vals = contract_gex(self.df).tolist()
        self.assertAlmostEqual(vals[0], 500_000.0)
        self.assertAlmostEqual(vals[1], -800_000.0)
        self.assertAlmostEqual(vals[2], 150_000.0)

    def test_net_gex_golden(self):
        from src.metrics import net_gex, regime
        net = net_gex(self.df)
        self.assertAlmostEqual(net, -150_000.0)
        self.assertEqual(regime(net), "-GEX")

    def test_gex_by_strike(self):
        from src.metrics import gex_by_strike
        by = gex_by_strike(self.df)
        self.assertAlmostEqual(by.loc[100.0], -300_000.0)
        self.assertAlmostEqual(by.loc[110.0], 150_000.0)

    def test_shares_convention(self):
        from src.config import EngineConfig
        from src.metrics import net_gex
        cfg = EngineConfig.from_dict({"metrics": {"gex_convention": "shares"}})
        # shares form drops the spot^2*0.01 factor: sign*gamma*OI*100.
        # 5000 - 8000 + 1500 = -1500.
        self.assertAlmostEqual(net_gex(self.df, config=cfg), -1500.0)

    def test_dealer_convention_flips_sign(self):
        from src.config import EngineConfig
        from src.metrics import net_gex
        cfg = EngineConfig.from_dict({"metrics": {"dealer_sign_convention": "short_call_long_put"}})
        self.assertAlmostEqual(net_gex(self.df, config=cfg), 150_000.0)

    def test_unknown_convention_raises(self):
        from src.config import EngineConfig
        from src.metrics import net_gex
        cfg = EngineConfig.from_dict({"metrics": {"dealer_sign_convention": "bogus"}})
        with self.assertRaises(ValueError):
            net_gex(self.df, config=cfg)


@unittest.skipUnless(_HAVE_STACK, "numpy/pandas not installed")
class TestBlackScholesGamma(unittest.TestCase):
    def test_atm_gamma_golden(self):
        from src.metrics import bs_gamma
        # d1 = (0 + 0.5*0.04)/0.2 = 0.1; pdf(0.1)=0.3969525; gamma=pdf/(100*0.2)=0.01984763.
        self.assertAlmostEqual(bs_gamma(100.0, 100.0, 1.0, 0.2, 0.0, 0.0), 0.01984763, places=7)

    def test_degenerate_inputs_are_zero(self):
        from src.metrics import bs_gamma
        self.assertEqual(bs_gamma(100.0, 100.0, 0.0, 0.2), 0.0)   # expired
        self.assertEqual(bs_gamma(100.0, 100.0, 1.0, 0.0), 0.0)   # no vol

    def test_vectorized(self):
        from src.metrics import bs_gamma
        out = bs_gamma(np.array([100.0, 100.0]), np.array([100.0, 100.0]),
                       np.array([1.0, 0.0]), np.array([0.2, 0.2]))
        self.assertAlmostEqual(out[0], 0.01984763, places=7)
        self.assertEqual(out[1], 0.0)


@unittest.skipUnless(_HAVE_STACK, "numpy/pandas not installed")
class TestZeroGex(unittest.TestCase):
    def _flip_chain(self):
        # Put OI low, call OI high: put gamma dominates below, call gamma above,
        # so Net GEX crosses zero between the strikes (a single clean flip).
        exp = dt.date(2024, 7, 3)  # 30 days out
        return mini_chain([
            {"type": "put",  "strike": 90,  "open_interest": 1000, "iv": 0.20, "expiration": exp},
            {"type": "call", "strike": 110, "open_interest": 1000, "iv": 0.20, "expiration": exp},
        ], spot=100.0)

    def test_zero_gex_is_a_real_sign_change(self):
        from src.metrics import bs_gamma, zero_gex
        df = self._flip_chain()
        z = zero_gex(df)
        self.assertIsNotNone(z)
        self.assertTrue(90.0 < z < 110.0, f"flip {z} not between the strikes")

        # Defining property: Net GEX (BS-recomputed) flips sign across z.
        K = np.array([90.0, 110.0]); oi = np.array([1000.0, 1000.0])
        sig = np.array([0.20, 0.20]); T = np.array([30 / 365.0, 30 / 365.0])
        signs = np.array([-1.0, 1.0])  # put -1, call +1
        r = 0.04

        def net_at(s):
            g = bs_gamma(s, K, T, sig, r, 0.0)
            return float(np.sum(signs * g * oi * 100 * (s * s * 0.01)))

        self.assertLess(net_at(z - 3), 0.0)
        self.assertGreater(net_at(z + 3), 0.0)

    def test_no_crossing_returns_none(self):
        from src.metrics import zero_gex
        exp = dt.date(2024, 7, 3)
        all_calls = mini_chain([
            {"type": "call", "strike": 100, "open_interest": 1000, "iv": 0.2, "expiration": exp},
            {"type": "call", "strike": 110, "open_interest": 1000, "iv": 0.2, "expiration": exp},
        ], spot=100.0)
        self.assertIsNone(zero_gex(all_calls))

    def test_gamma_snapshot(self):
        from src.metrics import gamma_snapshot
        snap = gamma_snapshot(self._flip_chain())
        self.assertEqual(snap.spot, 100.0)
        self.assertIn(snap.regime, {"+GEX", "-GEX", "flat"})
        self.assertIsNotNone(snap.zero_gex)


@unittest.skipUnless(_HAVE_STACK, "numpy/pandas not installed")
class TestEmptyChain(unittest.TestCase):
    def test_empty_is_safe(self):
        from src.metrics import net_gex, zero_gex
        empty = mini_chain([], spot=100.0)
        self.assertEqual(net_gex(empty), 0.0)
        self.assertIsNone(zero_gex(empty))


@unittest.skipUnless(_HAVE_STACK, "numpy/pandas not installed")
class TestSnapshotRigor(unittest.TestCase):
    def _chain(self):
        return mini_chain([
            {"type": "call", "strike": 100, "gamma": 0.05, "open_interest": 1000, "iv": 0.2},
            {"type": "put",  "strike": 100, "gamma": 0.04, "open_interest": 2000, "iv": 0.2},
        ], spot=100.0)

    def test_multi_snapshot_rejected(self):
        # F17: a frame carrying two distinct quote_ts (two days concatenated) must be
        # rejected, not silently mixed. The guard fires on symbol/quote_ts first.
        from src.metrics import gamma_snapshot
        two_days = pd.DataFrame({
            "symbol": ["T", "T"],
            "quote_ts": pd.to_datetime(["2024-06-03T20:00", "2024-06-04T20:00"], utc=True),
        })
        with self.assertRaises(ValueError):
            gamma_snapshot(two_days)

    def test_gamma_snapshot_flags(self):
        # F10 + F13: the snapshot carries the grid-hit and gamma-source-agreement flags.
        from src.metrics import gamma_snapshot
        snap = gamma_snapshot(self._chain())
        self.assertIsInstance(snap.zero_gex_in_grid, bool)
        self.assertIsInstance(snap.gamma_source_agrees, bool)

    def test_gamma_source_disagreement_flagged(self):
        # F13: vendor gamma (put-dominated, so -GEX) vs BS gamma at spot (ATM call
        # dominates, deep-OTM put ~0, so positive) disagree -> flag is False.
        from src.metrics import gamma_snapshot
        exp = dt.date(2024, 7, 3)
        df = mini_chain([
            {"type": "call", "strike": 100, "gamma": 0.01, "open_interest": 1000, "iv": 0.2, "expiration": exp},
            {"type": "put",  "strike": 60,  "gamma": 0.90, "open_interest": 1000, "iv": 0.2, "expiration": exp},
        ], spot=100.0)
        snap = gamma_snapshot(df)
        self.assertEqual(snap.regime, "-GEX")          # vendor gamma -> put-dominated
        self.assertFalse(snap.gamma_source_agrees)     # BS gamma at spot disagrees

    def test_net_gex_rejects_multi_snapshot(self):
        # F17 (fable follow-up): net_gex / zero_gex must guard too, not just the
        # composite entry points.
        from src.metrics import net_gex, zero_gex
        two_days = pd.DataFrame({
            "symbol": ["T", "T"],
            "quote_ts": pd.to_datetime(["2024-06-03T20:00", "2024-06-04T20:00"], utc=True),
        })
        with self.assertRaises(ValueError):
            net_gex(two_days)
        with self.assertRaises(ValueError):
            zero_gex(two_days)

    def test_zerogex_grid_from_config(self):
        # F10: a grid that doesn't span the flip -> None + zero_gex_in_grid False,
        # and the grid comes from (hashed) config, not a hard-coded kwarg.
        from src.config import EngineConfig
        from src.metrics import gamma_snapshot
        exp = dt.date(2024, 7, 3)
        df = mini_chain([
            {"type": "put",  "strike": 90,  "open_interest": 1000, "iv": 0.2, "expiration": exp},
            {"type": "call", "strike": 110, "open_interest": 1000, "iv": 0.2, "expiration": exp},
        ], spot=100.0)
        cfg = EngineConfig.from_dict({"metrics": {  # grid entirely above spot; flip is below
            "zerogex_grid_lo_frac": 1.05, "zerogex_grid_hi_frac": 1.10, "zerogex_grid_n": 11}})
        snap = gamma_snapshot(df, config=cfg)
        self.assertIsNone(snap.zero_gex)
        self.assertFalse(snap.zero_gex_in_grid)

    def test_greek_coverage(self):
        # F12: coverage stat over open interest.
        from src.metrics import greek_coverage
        cov = greek_coverage(self._chain())
        self.assertEqual(cov["n_contracts"], 2)
        self.assertEqual(cov["oi_total"], 3000.0)
        self.assertAlmostEqual(cov["oi_gamma_frac"], 1.0)   # both carry nonzero gamma


if __name__ == "__main__":
    unittest.main()
