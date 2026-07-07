"""Golden tests for Black-Scholes vanna/charm and their dealer exposures (Batch B).

Exact hand-computed vanna/charm values, a finite-difference cross-check of the
closed forms against numerically-differentiated delta, and hand-computed net
vanna/charm dealer exposures on a one-contract chain. Needs the data stack.

    .venv/bin/python -m unittest discover -s tests -v
"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import numpy as np
    from scipy.stats import norm
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False

from src.ingest import schema  # noqa: E402

_QD = dt.date(2024, 6, 3)
_EXP_1Y = dt.date(2025, 6, 3)   # exactly 365 days out => T = 1.0 under act/365


def mini_chain(contracts, *, spot, quote_date=_QD):
    """Build a valid canonical frame from partial contract dicts."""
    import pandas as pd
    qts = pd.Timestamp(quote_date.year, quote_date.month, quote_date.day, 20, 0, tz="UTC")
    rows = []
    for c in contracts:
        row = {name: None for name in schema.field_names()}
        row.update({
            "symbol": "TEST", "root": "TEST", "quote_ts": qts,
            "expiration": pd.Timestamp(c.get("expiration", _EXP_1Y)),
            "strike": float(c["strike"]), "type": c["type"],
            "underlying_price": float(spot),
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


def _bs_delta(S, K, T, sigma, r, q, is_call):
    """Reference BS delta for the finite-difference cross-check."""
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * np.sqrt(T))
    return np.exp(-q * T) * (norm.cdf(d1) if is_call else norm.cdf(d1) - 1.0)


@unittest.skipUnless(_HAVE_STACK, "numpy/scipy not installed")
class TestBsVanna(unittest.TestCase):
    def test_atm_vanna_golden(self):
        from src.metrics import bs_vanna
        # d1 = (0 + 0.5*0.04)/0.2 = 0.1; d2 = -0.1; pdf(0.1) = 0.39695255;
        # vanna = -pdf(d1)*d2/sigma = 0.39695255*0.1/0.2 = 0.19847627.
        self.assertAlmostEqual(bs_vanna(100.0, 100.0, 1.0, 0.2, 0.0, 0.0), 0.19847627, places=7)

    def test_degenerate_inputs_are_zero(self):
        from src.metrics import bs_vanna
        self.assertEqual(bs_vanna(100.0, 100.0, 0.0, 0.2), 0.0)   # expired
        self.assertEqual(bs_vanna(100.0, 100.0, 1.0, 0.0), 0.0)   # no vol

    def test_finite_difference_matches_delta_slope(self):
        # vanna = dDelta/dsigma (identical for call and put).
        from src.metrics import bs_vanna
        S, K, T, sig, r, q = 105.0, 100.0, 0.5, 0.25, 0.03, 0.01
        h = 1e-6
        fd = (_bs_delta(S, K, T, sig + h, r, q, True)
              - _bs_delta(S, K, T, sig - h, r, q, True)) / (2 * h)
        self.assertAlmostEqual(bs_vanna(S, K, T, sig, r, q), fd, places=6)


@unittest.skipUnless(_HAVE_STACK, "numpy/scipy not installed")
class TestBsCharm(unittest.TestCase):
    def test_charm_call_put_golden(self):
        # q=0.03 makes the call/put branches differ (the q*exp(-qT)*N(+/-d1) term).
        from src.metrics import bs_charm
        cc = bs_charm(100.0, 100.0, 1.0, 0.2, 0.0, 0.03, True)
        cp = bs_charm(100.0, 100.0, 1.0, 0.2, 0.0, 0.03, False)
        self.assertAlmostEqual(cc, 0.02364290, places=7)
        self.assertAlmostEqual(cp, -0.00547047, places=7)
        self.assertNotAlmostEqual(cc, cp)  # branches genuinely differ

    def test_degenerate_inputs_are_zero(self):
        from src.metrics import bs_charm
        self.assertEqual(bs_charm(100.0, 100.0, 0.0, 0.2, is_call=True), 0.0)
        self.assertEqual(bs_charm(100.0, 100.0, 1.0, 0.0, is_call=False), 0.0)

    def test_finite_difference_matches_delta_decay(self):
        # charm = dDelta/d(calendar time) = -dDelta/dT, per call and put.
        from src.metrics import bs_charm
        S, K, sig, r, q = 105.0, 100.0, 0.25, 0.03, 0.02
        T, h = 0.5, 1e-6
        for is_call in (True, False):
            fd = -(_bs_delta(S, K, T + h, sig, r, q, is_call)
                   - _bs_delta(S, K, T - h, sig, r, q, is_call)) / (2 * h)
            self.assertAlmostEqual(bs_charm(S, K, T, sig, r, q, is_call), fd, places=5)

    def test_vectorized_is_call_array(self):
        from src.metrics import bs_charm
        out = bs_charm(np.array([100.0, 100.0]), np.array([100.0, 100.0]),
                       np.array([1.0, 1.0]), np.array([0.2, 0.2]),
                       0.0, 0.03, np.array([True, False]))
        self.assertAlmostEqual(out[0], 0.02364290, places=7)
        self.assertAlmostEqual(out[1], -0.00547047, places=7)


@unittest.skipUnless(_HAVE_STACK, "numpy/scipy not installed")
class TestVannaExposure(unittest.TestCase):
    def _one_call(self, iv=0.2):
        # Default config: r=0.04, q=0. T=1.0. vanna(r=.04) = -0.190693908.
        return mini_chain([
            {"type": "call", "strike": 100, "open_interest": 1000, "iv": iv},
        ], spot=100.0)

    def test_net_vanna_golden(self):
        from src.metrics import net_vanna_exposure
        exp = net_vanna_exposure(self._one_call())
        # sign(+1) * (vanna * 0.01) * OI(1000) * 100 * spot(100)
        #   = -0.190693908 * 0.01 * 1000 * 100 * 100 = -19069.39.
        self.assertAlmostEqual(exp.net_vanna_proxy, -19069.39, places=2)
        self.assertEqual(exp.n_priced, 1)
        self.assertEqual(exp.n_skipped, 0)

    def test_skips_null_and_nonpositive_iv(self):
        from src.metrics import net_vanna_exposure
        df = mini_chain([
            {"type": "call", "strike": 100, "open_interest": 1000, "iv": 0.2},   # priced
            {"type": "put",  "strike": 90,  "open_interest": 1000, "iv": None},   # null IV -> skip
            {"type": "call", "strike": 110, "open_interest": 1000, "iv": 0.0},    # iv=0    -> skip
        ], spot=100.0)
        exp = net_vanna_exposure(df)
        self.assertEqual(exp.n_priced, 1)
        self.assertEqual(exp.n_skipped, 2)
        self.assertAlmostEqual(exp.net_vanna_proxy, -19069.39, places=2)

    def test_convention_flips_sign(self):
        from src.config import EngineConfig
        from src.metrics import net_vanna_exposure
        cfg = EngineConfig.from_dict({"metrics": {"dealer_sign_convention": "short_call_long_put"}})
        base = net_vanna_exposure(self._one_call()).net_vanna_proxy
        flipped = net_vanna_exposure(self._one_call(), config=cfg).net_vanna_proxy
        self.assertAlmostEqual(flipped, -base)

    def test_empty_is_safe(self):
        from src.metrics import net_vanna_exposure
        exp = net_vanna_exposure(mini_chain([], spot=100.0))
        self.assertEqual((exp.net_vanna_proxy, exp.n_priced, exp.n_skipped), (0.0, 0, 0))


@unittest.skipUnless(_HAVE_STACK, "numpy/scipy not installed")
class TestCharmExposure(unittest.TestCase):
    def _one_call(self):
        return mini_chain([
            {"type": "call", "strike": 100, "open_interest": 1000, "iv": 0.2},
        ], spot=100.0)

    def test_net_charm_golden(self):
        from src.metrics import net_charm_exposure
        exp = net_charm_exposure(self._one_call())
        # sign(+1) * (charm_call / 365) * OI(1000) * 100 * spot(100)
        #   charm_call(r=.04,q=0,T=1) = -0.057208172 => /365 => -0.000156735
        #   * 10,000,000 = -1567.35.
        self.assertAlmostEqual(exp.net_charm_proxy, -1567.35, places=2)
        self.assertEqual(exp.n_priced, 1)
        self.assertEqual(exp.n_skipped, 0)

    def test_skips_null_iv(self):
        from src.metrics import net_charm_exposure
        df = mini_chain([
            {"type": "call", "strike": 100, "open_interest": 1000, "iv": 0.2},
            {"type": "put",  "strike": 90,  "open_interest": 1000, "iv": None},
        ], spot=100.0)
        exp = net_charm_exposure(df)
        self.assertEqual(exp.n_priced, 1)
        self.assertEqual(exp.n_skipped, 1)
        self.assertAlmostEqual(exp.net_charm_proxy, -1567.35, places=2)

    def test_empty_is_safe(self):
        from src.metrics import net_charm_exposure
        exp = net_charm_exposure(mini_chain([], spot=100.0))
        self.assertEqual((exp.net_charm_proxy, exp.n_priced, exp.n_skipped), (0.0, 0, 0))


if __name__ == "__main__":
    unittest.main()
