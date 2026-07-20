"""Unit tests for the PURE dealer-sign calibration logic (stdlib-only).

Trade classification (the quote rule), bucketing, and the sign-map / fallback logic
are all stdlib-only at import time, so these run in the stdlib CI leg with no data
stack. Tiny synthetic fixtures, exact expected values. The data-stack pipeline
(aggregate / gex_rebuild) is covered separately in test_calibration_pipeline.py.

    .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.calibration import bucket, signmap  # noqa: E402
from src.calibration.classify import (  # noqa: E402
    BUY, INVALID, MID, SELL, classify_one, customer_sign_one,
    dealer_sign_from_customer, valid_nbbo,
)


class TestQuoteRule(unittest.TestCase):
    def test_at_or_above_ask_is_buy(self):
        self.assertEqual(classify_one(1.35, 1.29, 1.35), BUY)   # at ask
        self.assertEqual(classify_one(1.40, 1.29, 1.35), BUY)   # above ask

    def test_at_or_below_bid_is_sell(self):
        self.assertEqual(classify_one(1.29, 1.29, 1.35), SELL)  # at bid
        self.assertEqual(classify_one(1.20, 1.29, 1.35), SELL)  # below bid

    def test_inside_spread_by_proximity(self):
        # spread 1.29..1.35, mid 1.32
        self.assertEqual(classify_one(1.33, 1.29, 1.35), BUY)   # closer to ask
        self.assertEqual(classify_one(1.31, 1.29, 1.35), SELL)  # closer to bid

    def test_exact_midpoint_is_dropped(self):
        self.assertEqual(classify_one(1.32, 1.29, 1.35), MID)

    def test_invalid_nbbo(self):
        self.assertEqual(classify_one(1.30, 0.0, 1.35), INVALID)   # bid <= 0
        self.assertEqual(classify_one(1.30, 1.40, 1.35), INVALID)  # crossed (ask <= bid)
        self.assertEqual(classify_one(1.30, 1.35, 1.35), INVALID)  # locked (ask == bid)
        self.assertEqual(classify_one(1.30, None, 1.35), INVALID)  # missing bid
        self.assertEqual(classify_one(None, 1.29, 1.35), INVALID)  # missing price
        self.assertEqual(classify_one(0.0, 1.29, 1.35), INVALID)   # non-positive price

    def test_valid_nbbo_helper(self):
        self.assertTrue(valid_nbbo(1.0, 1.5))
        self.assertFalse(valid_nbbo(0.0, 1.5))
        self.assertFalse(valid_nbbo(1.5, 1.0))
        self.assertFalse(valid_nbbo(None, 1.0))

    def test_customer_sign_values(self):
        self.assertEqual(customer_sign_one(1.40, 1.29, 1.35), 1)   # buy
        self.assertEqual(customer_sign_one(1.20, 1.29, 1.35), -1)  # sell
        self.assertEqual(customer_sign_one(1.32, 1.29, 1.35), 0)   # mid
        self.assertEqual(customer_sign_one(1.30, 0.0, 1.35), 0)    # invalid

    def test_dealer_is_opposite_of_customer(self):
        self.assertEqual(dealer_sign_from_customer(5.0), -1)   # net buying -> dealer short
        self.assertEqual(dealer_sign_from_customer(-3.0), 1)   # net selling -> dealer long
        self.assertEqual(dealer_sign_from_customer(0.0), 0)


class TestVectorizedMatchesScalar(unittest.TestCase):
    """classify_vectorized must agree with classify_one row-by-row (needs numpy only)."""

    def test_agrees_on_edge_cases(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        from src.calibration.classify import classify_vectorized

        price = [1.35, 1.40, 1.29, 1.20, 1.33, 1.31, 1.32, 1.30, 1.30, float("nan")]
        bid = [1.29, 1.29, 1.29, 1.29, 1.29, 1.29, 1.29, 0.0, 1.40, 1.29]
        ask = [1.35, 1.35, 1.35, 1.35, 1.35, 1.35, 1.35, 1.35, 1.35, 1.35]
        cats, signs = classify_vectorized(np.array(price), np.array(bid), np.array(ask))
        for i in range(len(price)):
            self.assertEqual(cats[i], classify_one(price[i], bid[i], ask[i]), f"row {i}")


class TestBuckets(unittest.TestCase):
    def test_moneyness_band_edges_upper_inclusive(self):
        self.assertEqual(bucket.moneyness_band(95.0, 100.0), "<=0.95")   # 0.95 boundary
        self.assertEqual(bucket.moneyness_band(94.0, 100.0), "<=0.95")
        self.assertEqual(bucket.moneyness_band(97.0, 100.0), "0.95-0.99")
        self.assertEqual(bucket.moneyness_band(99.0, 100.0), "0.95-0.99")  # 0.99 boundary
        self.assertEqual(bucket.moneyness_band(100.0, 100.0), "0.99-1.01")
        self.assertEqual(bucket.moneyness_band(101.0, 100.0), "0.99-1.01")  # 1.01 boundary
        self.assertEqual(bucket.moneyness_band(103.0, 100.0), "1.01-1.05")
        self.assertEqual(bucket.moneyness_band(105.0, 100.0), "1.01-1.05")  # 1.05 boundary
        self.assertEqual(bucket.moneyness_band(110.0, 100.0), ">1.05")

    def test_moneyness_band_bad_inputs(self):
        self.assertIsNone(bucket.moneyness_band(0.0, 100.0))
        self.assertIsNone(bucket.moneyness_band(100.0, 0.0))
        self.assertIsNone(bucket.moneyness_band(None, 100.0))

    def test_dte_band_edges_upper_inclusive(self):
        self.assertEqual(bucket.dte_band(0), "0-7")
        self.assertEqual(bucket.dte_band(7), "0-7")
        self.assertEqual(bucket.dte_band(8), "8-30")
        self.assertEqual(bucket.dte_band(30), "8-30")
        self.assertEqual(bucket.dte_band(31), "31-60")
        self.assertEqual(bucket.dte_band(60), "31-60")

    def test_dte_band_outside_window(self):
        self.assertIsNone(bucket.dte_band(61))
        self.assertIsNone(bucket.dte_band(-1))
        self.assertIsNone(bucket.dte_band(None))

    def test_bucket_for_full_and_fallback(self):
        self.assertEqual(bucket.bucket_for("call", 105.0, 100.0, 5), "call|1.01-1.05|0-7")
        self.assertEqual(bucket.bucket_for("put", 95.0, 100.0, 45), "put|<=0.95|31-60")
        self.assertIsNone(bucket.bucket_for("call", 105.0, 100.0, 90))   # DTE > 60
        self.assertIsNone(bucket.bucket_for("warrant", 100.0, 100.0, 5))  # unknown type

    def test_key_roundtrip_and_count(self):
        key = bucket.bucket_key("call", "0.99-1.01", "0-7")
        self.assertEqual(bucket.parse_bucket_key(key), ("call", "0.99-1.01", "0-7"))
        self.assertEqual(len(bucket.all_bucket_keys()), 30)   # 2 x 5 x 3


class TestSignMap(unittest.TestCase):
    def _flows(self, bucket_name, flow_early, flow_late, n_each=6):
        """n_each sampled sessions in each half with a fixed net flow."""
        recs = []
        for i in range(n_each):
            recs.append({"session": f"2018-01-{i + 1:02d}", "bucket": bucket_name,
                         "net_flow": flow_early, "total_size": 100})
            recs.append({"session": f"2023-01-{i + 1:02d}", "bucket": bucket_name,
                         "net_flow": flow_late, "total_size": 100})
        return recs

    def test_dealer_sign_is_minus_flow_sign(self):
        # consistent customer BUYING (+) -> dealer SHORT (-1)
        m = signmap.build_sign_map(self._flows("call|0.99-1.01|0-7", 100, 120))
        s = m["call|0.99-1.01|0-7"]
        self.assertEqual(s["dealer_sign"], -1)
        self.assertTrue(s["stable"])

    def test_consistent_selling_gives_dealer_long(self):
        m = signmap.build_sign_map(self._flows("put|<=0.95|0-7", -80, -60))
        self.assertEqual(m["put|<=0.95|0-7"]["dealer_sign"], 1)
        self.assertTrue(m["put|<=0.95|0-7"]["stable"])

    def test_sign_flip_across_halves_is_unstable(self):
        # early net buying, late net selling -> halves disagree -> not stable
        m = signmap.build_sign_map(self._flows("call|>1.05|8-30", 100, -100))
        s = m["call|>1.05|8-30"]
        self.assertFalse(s["stable"])

    def test_insufficient_support_is_unstable(self):
        m = signmap.build_sign_map(self._flows("call|0.99-1.01|0-7", 50, 50, n_each=2))
        self.assertFalse(m["call|0.99-1.01|0-7"]["stable"])   # 4 total < min_sessions

    def test_one_sided_sample_is_unstable(self):
        # only early-half sessions (no late) -> late_n 0 -> not stable
        recs = [{"session": f"2018-01-{i+1:02d}", "bucket": "call|0.99-1.01|0-7",
                 "net_flow": 100, "total_size": 100} for i in range(10)]
        self.assertFalse(signmap.build_sign_map(recs)["call|0.99-1.01|0-7"]["stable"])

    def test_stable_lookup_only_keeps_stable_nonzero(self):
        recs = self._flows("call|0.99-1.01|0-7", 100, 120) + \
            self._flows("put|>1.05|8-30", 100, -100)   # second is unstable
        lk = signmap.stable_sign_lookup(signmap.build_sign_map(recs))
        self.assertIn("call|0.99-1.01|0-7", lk)
        self.assertNotIn("put|>1.05|8-30", lk)

    def test_empirical_contract_sign_uses_map_then_fallback(self):
        lk = signmap.stable_sign_lookup(
            signmap.build_sign_map(self._flows("call|0.99-1.01|0-7", 100, 120)))
        # in a stable bucket -> empirical (-1)
        self.assertEqual(signmap.empirical_contract_sign("call", 100.0, 100.0, 3, lk),
                         (-1, "empirical"))
        # DTE outside window -> fallback long_call_short_put (call -> +1)
        self.assertEqual(signmap.empirical_contract_sign("call", 100.0, 100.0, 90, lk),
                         (1, "fallback"))
        # put with no stable bucket -> fallback (put -> -1)
        self.assertEqual(signmap.empirical_contract_sign("put", 100.0, 100.0, 3, lk),
                         (-1, "fallback"))

    def test_fallback_sign_matches_long_call_short_put(self):
        self.assertEqual(signmap.fallback_sign("call"), 1)
        self.assertEqual(signmap.fallback_sign("put"), -1)
        self.assertEqual(signmap.fallback_sign("other"), 0)

    def test_perfectly_consistent_bucket_reports_inf_t(self):
        # zero variance, non-zero mean -> +/- inf t (not silently dropped)
        m = signmap.build_sign_map(self._flows("call|0.99-1.01|0-7", 100, 100))
        self.assertEqual(m["call|0.99-1.01|0-7"]["t_stat"], float("inf"))


if __name__ == "__main__":
    unittest.main()
