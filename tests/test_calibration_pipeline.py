"""Data-stack tests for the calibration pipeline (aggregate + gex_rebuild).

Tiny synthetic frames with exact expected values. Guarded on the data stack so the
stdlib CI leg (which has the PURE-logic tests in test_calibration_logic.py) stays
green. Covers window filtering, signed-flow aggregation, the regular-condition split,
and that the empirical GEX rebuild reduces to metrics.net_gex when no bucket is
stable (fallback == long_call_short_put).
"""

import datetime as dt
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

_SESSION = dt.date(2024, 6, 3)
_EXP_NEAR = dt.date(2024, 6, 7)     # DTE 4  -> band 0-7
_EXP_FAR = dt.date(2024, 9, 3)      # DTE 92 -> outside the DTE window


def trades_frame(rows):
    """Build a reduced trade frame like pull.read_cached returns."""
    recs = []
    for r in rows:
        rec = {
            "expiration": pd.Timestamp(r.get("expiration", _EXP_NEAR)),
            "strike": float(r["strike"]),
            "right": r["right"],
            "price": float(r["price"]),
            "size": int(r.get("size", 1)),
            "bid": float(r["bid"]),
            "ask": float(r["ask"]),
            "condition": int(r.get("condition", 18)),
            "ext_condition1": 255, "ext_condition2": 255,
            "ext_condition3": 255, "ext_condition4": 255,
        }
        recs.append(rec)
    return pd.DataFrame(recs)


def mini_chain(contracts, *, spot, session=_SESSION):
    """Valid canonical single-snapshot chain (mirrors test_flow_metrics.mini_chain)."""
    qts = pd.Timestamp(session.year, session.month, session.day, 20, 0, tz="UTC")
    rows = []
    for c in contracts:
        row = {name: None for name in schema.field_names()}
        row.update({
            "symbol": "TEST", "root": "TEST", "quote_ts": qts,
            "expiration": pd.Timestamp(c.get("expiration", _EXP_NEAR)),
            "strike": float(c["strike"]), "type": c["type"],
            "underlying_price": float(spot),
            "gamma": c.get("gamma"), "open_interest": c.get("open_interest"),
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
class TestAggregate(unittest.TestCase):
    def _classified(self):
        from src.calibration.aggregate import classify_session_trades
        rows = [
            # ATM call: buy 10 @ ask, sell 4 @ bid -> net +6 (call|0.99-1.01|0-7)
            {"strike": 100, "right": "CALL", "price": 1.35, "bid": 1.29, "ask": 1.35, "size": 10},
            {"strike": 100, "right": "CALL", "price": 1.29, "bid": 1.29, "ask": 1.35, "size": 4},
            # OTM put strike 95: sell 5 @ bid -> net -5 (put|<=0.95|0-7)
            {"strike": 95, "right": "PUT", "price": 0.50, "bid": 0.50, "ask": 0.60, "size": 5},
            # exact-midpoint call -> dropped from flow, counted as mid
            {"strike": 100, "right": "CALL", "price": 1.32, "bid": 1.29, "ask": 1.35, "size": 9},
            # outside moneyness window (strike 130 vs spot 100 -> 1.30) -> dropped
            {"strike": 130, "right": "CALL", "price": 0.10, "bid": 0.05, "ask": 0.15, "size": 3},
            # outside DTE window (far expiry) -> dropped
            {"strike": 100, "right": "CALL", "price": 2.0, "bid": 1.9, "ask": 2.1,
             "size": 3, "expiration": _EXP_FAR},
        ]
        return classify_session_trades(trades_frame(rows), 100.0, _SESSION)

    def test_window_filter_drops_out_of_window(self):
        cl = self._classified()
        # 6 input rows, 2 dropped (moneyness + DTE) -> 4 remain
        self.assertEqual(len(cl), 4)
        self.assertTrue((cl["dte"] <= 60).all())
        self.assertTrue((cl["moneyness"].between(0.85, 1.15)).all())

    def test_bucket_assignment(self):
        cl = self._classified()
        self.assertEqual(set(cl["bucket"]),
                         {"call|0.99-1.01|0-7", "put|<=0.95|0-7"})

    def test_signed_flow_per_bucket(self):
        from src.calibration.aggregate import aggregate_session
        recs = {r["bucket"]: r for r in aggregate_session(self._classified(), _SESSION)}
        self.assertAlmostEqual(recs["call|0.99-1.01|0-7"]["net_flow"], 6.0)   # 10 buy - 4 sell
        self.assertAlmostEqual(recs["put|<=0.95|0-7"]["net_flow"], -5.0)      # 5 sell
        self.assertEqual(recs["call|0.99-1.01|0-7"]["n_mid"], 1)              # midpoint counted

    def test_regular_condition_split(self):
        from src.calibration.aggregate import aggregate_session
        rows = [
            # regular (18) buy 10, multi-leg (130) buy 5 in same bucket
            {"strike": 100, "right": "CALL", "price": 1.35, "bid": 1.29, "ask": 1.35,
             "size": 10, "condition": 18},
            {"strike": 100, "right": "CALL", "price": 1.35, "bid": 1.29, "ask": 1.35,
             "size": 5, "condition": 130},
        ]
        from src.calibration.aggregate import classify_session_trades
        cl = classify_session_trades(trades_frame(rows), 100.0, _SESSION)
        rec = aggregate_session(cl, _SESSION)[0]
        self.assertAlmostEqual(rec["net_flow"], 15.0)       # all valid
        self.assertAlmostEqual(rec["net_flow_reg"], 10.0)   # regular (18) only

    def test_session_stats_shares(self):
        from src.calibration.aggregate import session_stats
        st = session_stats(self._classified())
        self.assertEqual(st["n_trades"], 4)
        self.assertAlmostEqual(st["buy_frac"] + st["sell_frac"] + st["mid_frac"]
                               + st["invalid_frac"], 1.0, places=6)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestGexRebuild(unittest.TestCase):
    def test_empirical_sign_applied(self):
        from src.calibration.gex_rebuild import empirical_net_gex
        chain = mini_chain([{"type": "call", "strike": 100, "gamma": 0.05,
                             "open_interest": 1000}], spot=100.0)
        lk = {"call|0.99-1.01|0-7": -1}
        # -1 * 0.05 * 1000 * 100 * (100^2 * 0.01) = -500,000
        res = empirical_net_gex(chain, lk)
        self.assertAlmostEqual(res["net_gex"], -500_000.0)
        self.assertAlmostEqual(res["fallback_gamma_oi_frac"], 0.0)   # bucket is stable

    def test_empty_lookup_reduces_to_long_call_short_put(self):
        from src.calibration.gex_rebuild import empirical_net_gex
        from src.metrics.gex import net_gex
        chain = mini_chain([
            {"type": "call", "strike": 100, "gamma": 0.05, "open_interest": 1000},
            {"type": "put", "strike": 95, "gamma": 0.04, "open_interest": 800},
        ], spot=100.0)
        res = empirical_net_gex(chain, {})   # no stable buckets -> all fallback
        self.assertAlmostEqual(res["net_gex"], net_gex(chain))
        self.assertAlmostEqual(res["fallback_gamma_oi_frac"], 1.0)

    def test_convention_agreement(self):
        from src.calibration.gex_rebuild import convention_agreement
        chain = mini_chain([{"type": "call", "strike": 100, "gamma": 0.05,
                             "open_interest": 1000}], spot=100.0)
        # empirical says dealer SHORT this ATM call (-1); long_call_short_put says +1
        agr = convention_agreement(chain, {"call|0.99-1.01|0-7": -1})
        self.assertAlmostEqual(agr["long_call_short_put"], 0.0)   # disagrees
        self.assertAlmostEqual(agr["stable_gamma_oi_frac"], 1.0)  # whole book scored

    def test_fallback_dte_beyond_window(self):
        from src.calibration.gex_rebuild import empirical_signs
        chain = mini_chain([{"type": "call", "strike": 100, "gamma": 0.05,
                             "open_interest": 1000, "expiration": _EXP_FAR}], spot=100.0)
        signs, is_fb = empirical_signs(chain, {"call|0.99-1.01|0-7": -1})
        self.assertTrue(bool(is_fb[0]))          # DTE 92 -> outside window -> fallback
        self.assertAlmostEqual(float(signs[0]), 1.0)   # fallback call sign +1


if __name__ == "__main__":
    unittest.main()
