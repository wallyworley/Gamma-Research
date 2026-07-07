"""ThetadataAdapter tests: greeks/OI join + PIT mapping, against a real recorded fixture.

Live HTTP (fetch_raw) is integration-tested manually with a key; the mapping logic
(normalize) is tested against a recorded AAPL 2026-07-02 slice plus a few injected edge
contracts: an OI-only row, a greeks-only row, an iv_error-flagged row (iv must null), and
an expired row (must drop). The index two-root case uses a small synthetic fixture. Needs
the data stack; skipped without it (the stdlib CI leg must still collect this module).
"""

import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pandas as pd  # noqa: F401
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False

from src.ingest import schema  # noqa: E402
from src.ingest.adapter import get_adapter  # noqa: E402

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "thetadata_sample.json")
_E1 = "2026-07-08"   # the fixture's live expiration (the session is 2026-07-02)


def _load_raw():
    with open(_FIXTURE) as fh:
        return json.load(fh)


def _greek_rec(raw, strike, right):
    for r in raw["roots"]["AAPL"]["greeks"]:
        if r["strike"] == strike and r["right"] == right and r["expiration"] == _E1:
            return r
    raise KeyError((strike, right))


def _oi_rec(raw, strike, right):
    for r in raw["roots"]["AAPL"]["open_interest"]:
        if r["strike"] == strike and r["right"] == right and r["expiration"] == _E1:
            return r
    raise KeyError((strike, right))


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestThetadataNormalize(unittest.TestCase):
    def setUp(self):
        from src.ingest.adapters.thetadata import ThetadataAdapter
        self.raw = _load_raw()
        self.df = ThetadataAdapter(api_key="test").normalize(self.raw, symbol="AAPL")

    def _row(self, strike, ctype, exp=_E1):
        m = ((self.df["strike"] == strike) & (self.df["type"] == ctype)
             & (self.df["expiration"].dt.strftime("%Y-%m-%d") == exp))
        rows = self.df[m]
        self.assertEqual(len(rows), 1, f"expected exactly one {ctype} @ {strike}/{exp}")
        return rows.iloc[0]

    def test_output_is_canonical_and_valid(self):
        # 4 matched + 1 greeks-only + 1 OI-only + 1 iv_error(kept) = 7; expired dropped.
        self.assertEqual(list(self.df.columns), schema.field_names())
        self.assertEqual(len(self.df), 7)
        self.assertEqual(schema.validate_frame(self.df), [])
        self.assertEqual(set(self.df["type"]), {"call", "put"})

    def test_symbol_and_root_for_equity(self):
        self.assertTrue((self.df["symbol"] == "AAPL").all())
        self.assertTrue((self.df["root"] == "AAPL").all())   # equity root == ticker

    def test_quote_ts_is_session_close_utc(self):
        ts = self.df["quote_ts"].iloc[0]
        self.assertEqual(str(ts.tz), "UTC")
        # 2026-07-02 16:00 EDT == 20:00 UTC
        self.assertEqual((ts.year, ts.month, ts.day, ts.hour), (2026, 7, 2, 20))

    def test_oi_asof_is_prior_trading_day(self):
        oa = self.df["oi_asof_date"].iloc[0].date()
        self.assertEqual(oa, dt.date(2026, 7, 1))            # Wed before Thu 7/2
        self.assertLess(oa, dt.date(2026, 7, 2))
        self.assertLess(oa.weekday(), 5)

    def test_spot_is_vendor_close(self):
        spot = self.df["underlying_price"].iloc[0]
        self.assertEqual(self.df["underlying_price"].nunique(), 1)     # one spot per chain
        self.assertAlmostEqual(spot, 308.63, places=2)
        self.assertTrue((self.df["_spot_source"] == "vendor_close").all())

    def test_provenance_columns(self):
        for col in ("_adapter", "_greek_source", "_iv_source"):
            self.assertTrue((self.df[col] == "thetadata").all())

    def test_field_mapping_golden(self):
        # A fully-populated contract: every canonical field maps from the vendor record
        # (close->last, right->type, dollars strike, implied_vol->iv, greeks straight).
        g = _greek_rec(self.raw, 300.0, "CALL")
        o = _oi_rec(self.raw, 300.0, "CALL")
        row = self._row(300.0, "call")
        self.assertEqual(row["last"], g["close"])
        self.assertEqual(int(row["volume"]), int(g["volume"]))
        self.assertEqual(row["bid"], g["bid"])
        self.assertEqual(row["ask"], g["ask"])
        self.assertEqual(row["delta"], g["delta"])
        self.assertEqual(row["gamma"], g["gamma"])
        self.assertEqual(row["theta"], g["theta"])
        self.assertEqual(row["vega"], g["vega"])
        self.assertEqual(row["rho"], g["rho"])
        self.assertEqual(row["iv"], g["implied_vol"])
        self.assertEqual(int(row["open_interest"]), int(o["open_interest"]))

    def test_greeks_only_row_has_null_oi(self):
        # 297.5 PUT is in greeks but not in open_interest -> greeks present, OI null.
        row = self._row(297.5, "put")
        self.assertTrue(pd.isna(row["open_interest"]))
        self.assertTrue(pd.notna(row["delta"]))
        self.assertTrue(pd.notna(row["iv"]))

    def test_oi_only_row_has_null_greeks(self):
        # 260 PUT is in open_interest only -> OI present, greeks/quotes null, spot still set.
        row = self._row(260.0, "put")
        self.assertEqual(int(row["open_interest"]), 777)
        for col in ("delta", "gamma", "theta", "vega", "rho", "iv", "bid", "ask", "last", "volume"):
            self.assertTrue(pd.isna(row[col]), f"{col} should be null on an OI-only row")
        self.assertTrue(pd.notna(row["underlying_price"]))

    def test_iv_error_nulls_iv_but_keeps_greeks(self):
        # 292.5 CALL carries iv_error=0.5 (> 0.05): iv is nulled (unreliable fit) while the
        # greeks and OI are retained.
        row = self._row(292.5, "call")
        self.assertTrue(pd.isna(row["iv"]))
        self.assertTrue(pd.notna(row["delta"]))
        self.assertTrue(pd.notna(row["open_interest"]))

    def test_expired_row_dropped(self):
        # The injected 2026-06-15 contract precedes the 2026-07-02 session -> not present.
        self.assertFalse((self.df["expiration"].dt.strftime("%Y-%m-%d") == "2026-06-15").any())

    def test_higher_order_greeks_dropped(self):
        # vanna/charm/vomma exist on the fixture's greeks records but are not canonical.
        self.assertNotIn("vanna", self.df.columns)
        self.assertNotIn("charm", self.df.columns)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestThetadataGuards(unittest.TestCase):
    def _adapter(self, **kw):
        from src.ingest.adapters.thetadata import ThetadataAdapter
        return ThetadataAdapter(api_key="test", **kw)

    def _greeks(self, strike, right, exp="2026-07-10", **over):
        rec = {"expiration": exp, "strike": float(strike), "right": right,
               "close": 5.0, "volume": 3, "bid": 4.9, "ask": 5.1,
               "delta": 0.5, "gamma": 0.02, "theta": -0.1, "vega": 9.0, "rho": 1.0,
               "implied_vol": 0.30, "iv_error": 0.0, "underlying_price": 100.0}
        rec.update(over)
        return rec

    def _oi(self, strike, right, exp="2026-07-10", oi=10):
        return {"expiration": exp, "strike": float(strike), "right": right, "open_interest": oi}

    def _raw(self, greeks, oi, symbol="AAPL", roots=None):
        return {"symbol": symbol, "session_date": "2026-07-02",
                "roots": roots or {symbol: {"greeks": greeks, "open_interest": oi}}}

    def test_no_positive_spot_raises(self):
        # underlying_price non-positive on every row -> refuse to write a spotless chain.
        raw = self._raw([self._greeks(100, "CALL", underlying_price=0.0)],
                        [self._oi(100, "CALL")])
        with self.assertRaises(ValueError):
            self._adapter().normalize(raw, symbol="AAPL")

    def test_oi_only_session_is_clean_skip(self):
        # A session with OI but ZERO greeks rows (before the vendor's per-symbol greeks
        # floor, e.g. SPX pre-2017) has no spot and no gamma: it must raise
        # NoDataForSession (the runner's clean skip), not a hard failure.
        from src.ingest.adapters.thetadata import NoDataForSession
        raw = self._raw([], [self._oi(100, "CALL", oi=1000)])
        with self.assertRaises(NoDataForSession):
            self._adapter().normalize(raw, symbol="SPX")

    def test_no_contracts_raises(self):
        raw = self._raw([], [])
        with self.assertRaises(ValueError):
            self._adapter().normalize(raw, symbol="AAPL")

    def test_index_two_roots_both_kept_no_collision(self):
        # SPX and SPXW at the same (exp, strike, type) are distinct OCC roots: both kept,
        # no OI dropped, no B2 collision (the dual-root fix, mirrored from massive).
        spx = ([self._greeks(5000, "CALL", underlying_price=5000.0)], [self._oi(5000, "CALL", oi=200000)])
        spxw = ([self._greeks(5000, "CALL", underlying_price=5000.0)], [self._oi(5000, "CALL", oi=500)])
        raw = self._raw(None, None, symbol="SPX", roots={
            "SPX": {"greeks": spx[0], "open_interest": spx[1]},
            "SPXW": {"greeks": spxw[0], "open_interest": spxw[1]}})
        df = self._adapter(index_roots=frozenset({"SPX"})).normalize(raw, symbol="SPX")
        at = df[df["strike"] == 5000.0]
        self.assertEqual(set(at["root"]), {"SPX", "SPXW"})
        self.assertTrue((at["symbol"] == "SPX").all())
        self.assertEqual(sorted(int(x) for x in at["open_interest"]), [500, 200000])

    def test_settlement_collision_same_root_fails_loud(self):
        # Two DISTINCT contracts under the SAME root sharing the full key with different OI
        # would silently drop the larger side if collapsed -> fail loud (B2).
        raw = self._raw(
            [self._greeks(5000, "CALL", underlying_price=5000.0, delta=0.9),
             self._greeks(5000, "CALL", underlying_price=5000.0, delta=0.9, close=6.0)],
            [self._oi(5000, "CALL", oi=200000), self._oi(5000, "CALL", oi=500)])
        with self.assertRaises(NotImplementedError):
            self._adapter().normalize(raw, symbol="SPX")

    def test_exact_duplicate_rows_collapse(self):
        # A byte-identical greeks repeat collapses quietly (a vendor artifact, not a
        # collision); its single OI row survives.
        g = self._greeks(100, "PUT", underlying_price=100.0)
        raw = self._raw([g, dict(g)], [self._oi(100, "PUT")])
        df = self._adapter().normalize(raw, symbol="X")
        self.assertEqual(len(df), 1)
        self.assertEqual(int(df["open_interest"].iloc[0]), 10)

    def test_malformed_records_skipped_not_fatal(self):
        # A record missing strike/right/expiration is skipped; the good one survives.
        bad = {"expiration": _E1, "right": "CALL"}     # no strike
        raw = self._raw([self._greeks(100, "CALL", underlying_price=100.0), bad],
                        [self._oi(100, "CALL")])
        df = self._adapter().normalize(raw, symbol="AAPL")
        self.assertEqual(len(df), 1)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestThetadataPipeline(unittest.TestCase):
    def test_registration_and_write_read_roundtrip(self):
        import tempfile

        from src.ingest import io
        from src.ingest.adapters.thetadata import ThetadataAdapter
        import src.ingest.adapters  # noqa: F401  (triggers registration)

        self.assertIs(get_adapter("thetadata"), ThetadataAdapter)
        df = ThetadataAdapter(api_key="test").normalize(_load_raw(), symbol="AAPL")
        qd = df["quote_ts"].iloc[0].date()
        with tempfile.TemporaryDirectory() as root:
            io.write_canonical(df, root, "AAPL", qd)
            back = io.read_canonical(root, "AAPL", qd)
        self.assertEqual(len(back), 7)
        self.assertEqual(schema.validate_frame(back), [])
        self.assertEqual(set(back["type"]), {"call", "put"})


if __name__ == "__main__":
    unittest.main()
