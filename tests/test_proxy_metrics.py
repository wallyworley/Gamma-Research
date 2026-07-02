"""Golden tests for the M3 proxy suite: DEX, GEX Ratio, OI levels, grade.

Hand-computed exact values on mini-chains, config-driven convention switches, and
property tests for the owned grade composite. Needs the data stack.

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

_QD = dt.date(2024, 6, 3)
_EXP = dt.date(2024, 7, 19)


def mini_chain(contracts, *, spot, quote_date=_QD):
    """Build a valid canonical frame from partial contract dicts."""
    qts = pd.Timestamp(quote_date.year, quote_date.month, quote_date.day, 20, 0, tz="UTC")
    rows = []
    for c in contracts:
        row = {name: None for name in schema.field_names()}
        row.update({
            "symbol": "TEST", "quote_ts": qts,
            "expiration": pd.Timestamp(c.get("expiration", _EXP)),
            "strike": float(c["strike"]), "type": c["type"],
            "underlying_price": float(spot),
            "gamma": c.get("gamma"), "delta": c.get("delta"),
            "open_interest": c.get("open_interest"), "iv": c.get("iv"),
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


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestDex(unittest.TestCase):
    def setUp(self):
        # spot=100; per-contract DEX = sign * delta * OI * 100 * 100.
        self.df = mini_chain([
            {"type": "call", "strike": 105, "delta": 0.45, "open_interest": 8000},   # above: +36,000,000
            {"type": "call", "strike": 110, "delta": 0.30, "open_interest": 15000},  # above: +45,000,000
            {"type": "put",  "strike": 95,  "delta": -0.40, "open_interest": 12000}, # below: +48,000,000
            {"type": "put",  "strike": 90,  "delta": -0.25, "open_interest": 9000},  # below: +22,500,000
        ], spot=100.0)

    def test_balance_golden(self):
        from src.metrics import dealer_delta_balance
        bal = dealer_delta_balance(self.df)
        self.assertAlmostEqual(bal.above_proxy, 81_000_000.0)
        self.assertAlmostEqual(bal.below_proxy, 70_500_000.0)
        self.assertAlmostEqual(bal.at_proxy, 0.0)
        self.assertAlmostEqual(bal.net_proxy, 151_500_000.0)
        self.assertAlmostEqual(bal.skew_proxy, 10_500_000.0 / 151_500_000.0)

    def test_dealer_convention_flips_dex(self):
        from src.config import EngineConfig
        from src.metrics import dealer_delta_balance
        cfg = EngineConfig.from_dict({"metrics": {"dealer_sign_convention": "short_call_long_put"}})
        self.assertAlmostEqual(dealer_delta_balance(self.df, config=cfg).net_proxy, -151_500_000.0)

    def test_db_change(self):
        from src.metrics import db_change
        out = db_change(pd.Series([100.0, 150.0, 130.0])).tolist()
        self.assertTrue(math.isnan(out[0]))
        self.assertAlmostEqual(out[1], 50.0)
        self.assertAlmostEqual(out[2], -20.0)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestGexRatio(unittest.TestCase):
    def test_balanced_ratio_is_one(self):
        from src.metrics import gex_ratio
        df = mini_chain([
            {"type": "call", "strike": 100, "gamma": 0.03, "open_interest": 1000},
            {"type": "put",  "strike": 100, "gamma": 0.03, "open_interest": 1000},
        ], spot=100.0)
        self.assertAlmostEqual(gex_ratio(df), 1.0)

    def test_put_heavy_ratio_below_one(self):
        from src.metrics import gex_ratio
        df = mini_chain([
            {"type": "call", "strike": 100, "gamma": 0.02, "open_interest": 1000},   # 2,000,000
            {"type": "put",  "strike": 100, "gamma": 0.04, "open_interest": 2000},   # 8,000,000
        ], spot=100.0)
        self.assertAlmostEqual(gex_ratio(df), 2_000_000.0 / 8_000_000.0)

    def test_no_puts_is_inf(self):
        from src.metrics import gex_ratio
        df = mini_chain([{"type": "call", "strike": 100, "gamma": 0.03, "open_interest": 1000}], spot=100.0)
        self.assertEqual(gex_ratio(df), float("inf"))

    def test_trailing_percentile(self):
        from src.metrics import trailing_percentile
        s = pd.Series([1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(trailing_percentile(s, 3.0), 0.75)
        self.assertAlmostEqual(trailing_percentile(s), 1.0)  # last value = 4 -> top
        self.assertTrue(math.isnan(trailing_percentile(pd.Series([], dtype=float))))


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestOiLevels(unittest.TestCase):
    def test_coi_poi(self):
        from src.metrics import oi_levels
        df = mini_chain([
            {"type": "call", "strike": 105, "open_interest": 8000},
            {"type": "call", "strike": 110, "open_interest": 15000},
            {"type": "put",  "strike": 95,  "open_interest": 12000},
            {"type": "put",  "strike": 90,  "open_interest": 9000},
        ], spot=100.0)
        lv = oi_levels(df)
        self.assertEqual(lv.coi_level, 110.0)
        self.assertEqual(lv.poi_level, 95.0)
        self.assertEqual(lv.coi_total, 23000.0)
        self.assertEqual(lv.poi_total, 21000.0)

    def test_moneyness_grid(self):
        from src.metrics import moneyness_levels
        df = mini_chain([
            {"type": "put",  "strike": 95,  "open_interest": 12000},  # OTM put
            {"type": "put",  "strike": 90,  "open_interest": 20000},  # OTM put (bigger) -> COTMP
            {"type": "put",  "strike": 105, "open_interest": 8000},   # ITM put  -> CITMP
            {"type": "call", "strike": 110, "open_interest": 15000},  # OTM call -> COTMC
            {"type": "call", "strike": 108, "open_interest": 5000},   # OTM call
            {"type": "call", "strike": 95,  "open_interest": 7000},   # ITM call -> CITMC
        ], spot=100.0)
        ml = moneyness_levels(df)
        self.assertEqual(ml.cotmp_proxy, 90.0)
        self.assertEqual(ml.citmp_proxy, 105.0)
        self.assertEqual(ml.cotmc_proxy, 110.0)
        self.assertEqual(ml.citmc_proxy, 95.0)

    def test_empty_bucket_is_none(self):
        from src.metrics import moneyness_levels
        df = mini_chain([{"type": "call", "strike": 110, "open_interest": 15000}], spot=100.0)
        ml = moneyness_levels(df)
        self.assertEqual(ml.cotmc_proxy, 110.0)
        self.assertIsNone(ml.cotmp_proxy)
        self.assertIsNone(ml.citmp_proxy)
        self.assertIsNone(ml.citmc_proxy)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestTransitions(unittest.TestCase):
    def test_ptrans_ntrans(self):
        from src.metrics import gamma_transitions
        df = mini_chain([
            {"type": "call", "strike": 105, "gamma": 0.04, "open_interest": 5000},   # call-dominated above
            {"type": "call", "strike": 110, "gamma": 0.03, "open_interest": 4000},
            {"type": "call", "strike": 102, "gamma": 0.01, "open_interest": 100},     # small call...
            {"type": "put",  "strike": 102, "gamma": 0.05, "open_interest": 5000},    # ...put dominates at 102
            {"type": "put",  "strike": 95,  "gamma": 0.04, "open_interest": 5000},    # put-dominated below
            {"type": "put",  "strike": 90,  "gamma": 0.03, "open_interest": 4000},
        ], spot=100.0)
        t = gamma_transitions(df)
        self.assertEqual(t.ptrans_proxy, 105.0)  # 102 is put-dominated, so first call-dominated above is 105
        self.assertEqual(t.ntrans_proxy, 95.0)

    def test_no_dominance_is_none(self):
        from src.metrics import gamma_transitions
        # Equal call/put gamma exposure at each strike -> dominance 0 everywhere.
        df = mini_chain([
            {"type": "call", "strike": 110, "gamma": 0.03, "open_interest": 1000},
            {"type": "put",  "strike": 110, "gamma": 0.03, "open_interest": 1000},
            {"type": "call", "strike": 90,  "gamma": 0.03, "open_interest": 1000},
            {"type": "put",  "strike": 90,  "gamma": 0.03, "open_interest": 1000},
        ], spot=100.0)
        t = gamma_transitions(df)
        self.assertIsNone(t.ptrans_proxy)
        self.assertIsNone(t.ntrans_proxy)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestGradeProxy(unittest.TestCase):
    def _chain(self, kinds):
        rows = []
        for i, k in enumerate(kinds):
            strike = 90 + i * 5
            rows.append({"type": k, "strike": strike, "gamma": 0.03,
                         "delta": 0.4 if k == "call" else -0.4, "open_interest": 5000, "iv": 0.2})
        return mini_chain(rows, spot=100.0)

    def test_composite_quarantined_by_default(self):
        # F8: score_proxy is None unless explicitly enabled; components always present.
        from src.metrics import grade_proxy
        g = grade_proxy(self._chain(["call", "put", "call", "put"]))
        self.assertIsNone(g.score_proxy)
        self.assertEqual(set(g.components), {
            "regime", "gex_ratio_pct", "delta_skew", "dist_zerogex", "oi_proximity"})

    def test_composite_in_range_when_enabled(self):
        from src.metrics import grade_proxy
        g = grade_proxy(self._chain(["call", "put", "call", "put"]), enable_composite=True)
        self.assertTrue(0.0 <= g.score_proxy <= 10.0)

    def test_call_gamma_scores_higher_than_put_gamma(self):
        from src.metrics import grade_proxy
        all_calls = grade_proxy(self._chain(["call", "call", "call"]), enable_composite=True)
        all_puts = grade_proxy(self._chain(["put", "put", "put"]), enable_composite=True)
        self.assertAlmostEqual(all_calls.components["regime"], 1.0)
        self.assertAlmostEqual(all_puts.components["regime"], 0.0)
        self.assertGreater(all_calls.score_proxy, all_puts.score_proxy)

    def test_weights_normalized_keeps_range(self):
        from src.metrics import grade_proxy
        # Non-normalized weights must still yield a score in [0, 10].
        g = grade_proxy(self._chain(["call", "put"]), enable_composite=True,
                        weights={"regime": 5, "gex_ratio_pct": 5, "delta_skew": 5,
                                 "dist_zerogex": 5, "oi_proximity": 5})
        self.assertTrue(0.0 <= g.score_proxy <= 10.0)

    def test_empty_is_none(self):
        from src.metrics import grade_proxy
        g = grade_proxy(mini_chain([], spot=100.0), enable_composite=True)
        self.assertIsNone(g.score_proxy)
        self.assertEqual(g.components, {})


if __name__ == "__main__":
    unittest.main()
