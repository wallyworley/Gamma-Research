"""MassiveAdapter tests: session-from-snapshot + greek-implied spot, against a real fixture.

Live HTTP (fetch_raw) is integration-tested manually with a key; the derivation logic
(normalize + the _util helpers) is tested against a recorded snapshot. The fixture is a
real AAPL 7/2 near-money slice plus three injected edge contracts (a stale-day-bar
contract for R1, a malformed-expiry contract for R4, a deep null-IV put). Needs the data
stack; skipped without it.
"""

import datetime as dt
import json
import os
import sys
import unittest
from math import log, sqrt
from statistics import NormalDist

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pandas as pd  # noqa: F401
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False

from src.ingest import schema  # noqa: E402
from src.ingest.adapter import get_adapter  # noqa: E402

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "massive_options_sample.json")


def _load_raw():
    with open(_FIXTURE) as fh:
        return json.load(fh)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestUtilHelpers(unittest.TestCase):
    """Pure helpers underpinning the spot/session derivation."""

    def test_et_date_from_epoch_ns(self):
        from src.ingest.adapters._util import et_date_from_epoch_ns
        # 1782964800e9 ns = 2026-07-02 00:00 America/New_York
        self.assertEqual(et_date_from_epoch_ns(1782964800000000000), dt.date(2026, 7, 2))
        self.assertIsNone(et_date_from_epoch_ns(None))
        self.assertIsNone(et_date_from_epoch_ns(""))

    def test_bs_implied_spot_roundtrips(self):
        from src.ingest.adapters._util import bs_implied_spot
        N = NormalDist()
        S, K, iv, tau, r = 100.0, 105.0, 0.25, 30 / 365, 0.045
        d1 = (log(S / K) + (r + 0.5 * iv * iv) * tau) / (iv * sqrt(tau))
        call_delta = N.cdf(d1)
        put_delta = call_delta - 1.0
        self.assertAlmostEqual(bs_implied_spot(K, iv, call_delta, tau, True, r), S, places=4)
        self.assertAlmostEqual(bs_implied_spot(K, iv, put_delta, tau, False, r), S, places=4)

    def test_occ_root(self):
        from src.ingest.adapters._util import occ_root
        self.assertEqual(occ_root("O:SPXW260706C03000000"), "SPXW")
        self.assertEqual(occ_root("O:SPX260706C03000000"), "SPX")
        self.assertEqual(occ_root("O:AAPL260717C00300000"), "AAPL")
        self.assertEqual(occ_root("O:AAPL1260717C00300000"), "AAPL1")   # adjusted root
        self.assertIsNone(occ_root(None))
        self.assertIsNone(occ_root(""))
        self.assertIsNone(occ_root("short"))
        # a long but non-OSI ticker must NOT mint a wrong root (shape-checked tail)
        self.assertIsNone(occ_root("O:NOTAREALOSITICKERSTRING"))

    def test_bs_implied_spot_rejects_bad_inputs(self):
        from src.ingest.adapters._util import bs_implied_spot
        self.assertIsNone(bs_implied_spot(None, 0.2, 0.5, 0.1, True))
        self.assertIsNone(bs_implied_spot(100, 0.0, 0.5, 0.1, True))     # iv<=0
        self.assertIsNone(bs_implied_spot(100, 0.2, 1.0, 0.1, True))     # N(d1) not in (0,1)
        self.assertIsNone(bs_implied_spot(100, 0.2, 0.0, 0.1, False))    # put delta -> N(d1)=1


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestMassiveNormalize(unittest.TestCase):
    def setUp(self):
        from src.ingest.adapters.massive import MassiveAdapter
        self.df = MassiveAdapter(api_key="test").normalize(_load_raw(), symbol="AAPL")

    def test_output_is_canonical_and_valid(self):
        self.assertEqual(list(self.df.columns), schema.field_names())
        # 28 real + stale(288C) + deep(150P) kept; malformed-expiry dropped.
        self.assertEqual(len(self.df), 30)
        self.assertEqual(schema.validate_frame(self.df), [])
        self.assertEqual(set(self.df["type"]), {"call", "put"})

    def test_session_derived_from_snapshot(self):
        # max(day.last_updated) ET = 2026-07-02 -> quote_ts 16:00 EDT = 20:00 UTC.
        ts = self.df["quote_ts"].iloc[0]
        self.assertEqual(str(ts.tz), "UTC")
        self.assertEqual((ts.year, ts.month, ts.day, ts.hour), (2026, 7, 2, 20))

    def test_oi_asof_prior_weekday(self):
        oa = self.df["oi_asof_date"].iloc[0].date()
        self.assertLess(oa, dt.date(2026, 7, 2))
        self.assertLess(oa.weekday(), 5)

    def test_spot_recovered_from_greeks_not_stale_close(self):
        # Delta-inversion recovers the true 7/2 spot (~308), NOT the stale /prev close
        # (294.38) the buggy adapter used. One spot for the whole frame.
        spot = self.df["underlying_price"].iloc[0]
        self.assertTrue((self.df["underlying_price"] == spot).all())
        self.assertTrue(307.0 < spot < 309.0, f"spot={spot}")
        self.assertGreater(spot, 300.0)                 # proves we're not on 294.38

    def test_rho_is_null(self):
        self.assertTrue(self.df["rho"].isna().all())

    def test_provenance_columns(self):
        for col in ("_adapter", "_greek_source", "_iv_source"):
            self.assertTrue((self.df[col] == "massive").all())
        # spot came from the tight (primary) delta-inversion tier on this fixture.
        self.assertTrue((self.df["_spot_source"] == "implied_delta_t0").all())

    def test_stale_day_bar_is_nulled_R1(self):
        # The 288C carries a day bar from a prior session (6/1); its last/volume must be
        # nulled rather than stamped as the 7/2 session's, but OI (not day-scoped) stays.
        stale = self.df[(self.df["type"] == "call") & (self.df["strike"] == 288.0)].iloc[0]
        self.assertTrue(pd.isna(stale["last"]))
        self.assertTrue(pd.isna(stale["volume"]))
        self.assertEqual(int(stale["open_interest"]), 42)

    def test_deep_null_iv_maps_through(self):
        # Deep 150P: no IV/greeks from the vendor -> null (not faked 0); fresh day bar kept.
        deep = self.df[(self.df["type"] == "put") & (self.df["strike"] == 150.0)].iloc[0]
        self.assertTrue(pd.isna(deep["iv"]))
        self.assertTrue(pd.isna(deep["delta"]))
        self.assertTrue(pd.isna(deep["bid"]))
        self.assertEqual(int(deep["open_interest"]), 100)

    def test_a_real_contract_has_greeks(self):
        real = self.df[(self.df["strike"] >= 305.0) & (self.df["strike"] <= 312.0)
                       & self.df["iv"].notna()]
        self.assertGreater(len(real), 0)
        self.assertTrue((real["gamma"] > 0).any())


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestMassiveDerivationGuards(unittest.TestCase):
    def _adapter(self):
        from src.ingest.adapters.massive import MassiveAdapter
        return MassiveAdapter(api_key="test")

    def test_no_day_timestamps_raises(self):
        # No day.last_updated anywhere -> session cannot be derived.
        raw = {"results": [{"details": {"contract_type": "call", "strike_price": 100.0,
                                        "expiration_date": "2026-07-10"},
                            "greeks": {"delta": 0.5}, "implied_volatility": 0.3}]}
        with self.assertRaises(ValueError):
            self._adapter().normalize(raw, symbol="AAPL")

    def test_unreliable_spot_raises(self):
        # Session derivable, but no near-ATM greeks -> spot cannot be recovered.
        raw = {"results": [{"details": {"contract_type": "call", "strike_price": 100.0,
                                        "expiration_date": "2026-07-10"},
                            "greeks": {"delta": 0.99}, "implied_volatility": 0.3,
                            "day": {"last_updated": 1782964800000000000}}]}
        with self.assertRaises(ValueError):
            self._adapter().normalize(raw, symbol="AAPL")

    def test_vendor_close_overrides_delta_inversion(self):
        # An authoritative underlying_close (future entitled tier) bypasses inversion.
        raw = _load_raw()
        raw["underlying_close"] = 250.0
        raw["session_date"] = "2026-07-02"
        df = self._adapter().normalize(raw, symbol="AAPL")
        self.assertTrue((df["underlying_price"] == 250.0).all())
        self.assertTrue((df["_spot_source"] == "vendor_close").all())

    def _near_atm(self, n=5):
        sess, lu = dt.date(2026, 7, 2), 1782964800000000000
        return [{"details": {"contract_type": "call", "strike_price": 100.0,
                             "expiration_date": (sess + dt.timedelta(days=20 + i)).isoformat()},
                 "greeks": {"delta": 0.5, "gamma": 0.02}, "implied_volatility": 0.30,
                 "day": {"last_updated": lu}} for i in range(n)]

    def test_settlement_collision_fails_loud(self):
        # Two DISTINCT contracts (AM SPX vs PM SPXW) share (exp, strike, type) with different
        # OI: collapsing would silently drop the larger side, so fail loud (B2), not corrupt.
        lu = 1782964800000000000
        collide = [{"details": {"contract_type": "call", "strike_price": 500.0,
                                "expiration_date": "2026-07-10"},
                    "greeks": {"delta": 0.99, "gamma": 0.001}, "implied_volatility": 0.30,
                    "open_interest": oi, "day": {"last_updated": lu}} for oi in (200000, 500)]
        with self.assertRaises(NotImplementedError):
            self._adapter().normalize({"results": self._near_atm() + collide}, symbol="SPX")

    def test_am_pm_roots_both_kept(self):
        # AM-settled SPX and PM-settled SPXW at the same strike/expiry/type are distinct
        # OCC roots -> kept as separate rows (no OI dropped), the dual-root fix.
        lu = 1782964800000000000
        am_pm = [{"details": {"contract_type": "call", "strike_price": 500.0,
                              "expiration_date": "2026-07-10", "ticker": tk},
                  "greeks": {"delta": 0.99, "gamma": 0.001}, "implied_volatility": 0.30,
                  "open_interest": oi, "day": {"last_updated": lu}}
                 for tk, oi in (("O:SPX260710C05000000", 200000), ("O:SPXW260710C05000000", 500))]
        df = self._adapter().normalize({"results": self._near_atm() + am_pm}, symbol="SPX")
        at500 = df[df["strike"] == 500.0]
        self.assertEqual(set(at500["root"]), {"SPX", "SPXW"})
        self.assertEqual(sorted(int(x) for x in at500["open_interest"]), [500, 200000])

    def test_exact_duplicate_rows_collapse(self):
        # Byte-identical vendor repeats collapse quietly (not a settlement collision).
        lu = 1782964800000000000
        dupe = {"details": {"contract_type": "put", "strike_price": 100.0,
                            "expiration_date": "2026-07-20"},
                "greeks": {"delta": -0.5, "gamma": 0.02}, "implied_volatility": 0.30,
                "open_interest": 10, "day": {"last_updated": lu}}
        df = self._adapter().normalize({"results": self._near_atm() + [dupe, dict(dupe)]},
                                       symbol="X")
        self.assertEqual(len(df), 6)   # 5 calls + the put once (dup collapsed)

    def test_index_ticker_mapping(self):
        # Cash indices fetch with the Polygon `I:` prefix but stay plain in the canonical
        # symbol/partition; non-index symbols are untouched.
        from src.ingest.adapters.massive import MassiveAdapter
        a = MassiveAdapter(api_key="test", index_roots=frozenset({"SPX", "NDX"}))
        self.assertEqual(a._polygon_ticker("SPX"), "I:SPX")
        self.assertEqual(a._polygon_ticker("NDX"), "I:NDX")
        self.assertEqual(a._polygon_ticker("AAPL"), "AAPL")
        # default: no roots -> nothing is prefixed
        self.assertEqual(MassiveAdapter(api_key="test")._polygon_ticker("SPX"), "SPX")

    def test_all_malformed_raises(self):
        raw = {"results": [{"details": {}}]}   # no strike/type/expiration, no day stamp
        with self.assertRaises(ValueError):
            self._adapter().normalize(raw, symbol="AAPL")

    def test_thin_chain_recovered_by_fallback_tier(self):
        # A thin chain with contracts only at 75-105 days out fails the tight tier
        # (tau > 60d) but the wider fallback tier recovers a sane spot (~100).
        results = [{"details": {"contract_type": "call", "strike_price": 100.0,
                                "expiration_date": exp},
                    "greeks": {"delta": 0.55, "gamma": 0.02}, "implied_volatility": 0.30,
                    "open_interest": 50,
                    "day": {"last_updated": 1782964800000000000, "close": 5.0, "volume": 3}}
                   for exp in ("2026-09-15", "2026-09-25", "2026-10-05", "2026-10-15")]
        df = self._adapter().normalize({"results": results}, symbol="THIN")
        self.assertEqual(len(df), 4)
        spot = df["underlying_price"].iloc[0]
        self.assertTrue(98.0 < spot < 101.0, f"spot={spot}")

    def test_tier_order_prefers_primary_when_both_qualify(self):
        # Both tiers can qualify but imply different medians; the tight primary tier must
        # win (guards against a fallback-first regression the fixture can't catch).
        sess, lu = dt.date(2026, 7, 2), 1782964800000000000

        def mk(days, strike, n):
            return [{"details": {"contract_type": "call", "strike_price": float(strike),
                                 "expiration_date": (sess + dt.timedelta(days=days + i)).isoformat()},
                     "greeks": {"delta": 0.5, "gamma": 0.02}, "implied_volatility": 0.30,
                     "day": {"last_updated": lu}} for i in range(n)]
        short = mk(28, 100.0, 6)   # tau ~30d -> implies ~99.3, qualifies the tight tier
        long_ = mk(98, 70.0, 6)    # tau ~100d -> implies ~68, only reachable via fallback
        df = self._adapter().normalize({"results": short + long_}, symbol="TIER")
        spot = df["underlying_price"].iloc[0]
        # tight-tier median (~99), NOT the fallback/blended value (~68-84)
        self.assertGreater(spot, 95.0)

    def test_widened_delta_band_used_by_fallback(self):
        # ncdf=0.27 is outside the tight 0.30-0.70 band but inside the fallback 0.25-0.75;
        # a chain of only such (short-tau) contracts fails the tight tier on delta alone
        # and is recovered by the fallback.
        sess, lu = dt.date(2026, 7, 2), 1782964800000000000
        results = [{"details": {"contract_type": "call", "strike_price": 100.0,
                                "expiration_date": (sess + dt.timedelta(days=38 + 2 * i)).isoformat()},
                    "greeks": {"delta": 0.27, "gamma": 0.02}, "implied_volatility": 0.30,
                    "day": {"last_updated": lu}} for i in range(4)]
        df = self._adapter().normalize({"results": results}, symbol="BAND")
        self.assertEqual(len(df), 4)
        self.assertTrue(90.0 < df["underlying_price"].iloc[0] < 96.0)

    def test_non_string_expiration_is_skipped_not_fatal(self):
        # A non-string expiration_date (int) must be skipped like any malformed date,
        # not abort the load with an uncaught TypeError.
        raw = _load_raw()
        raw["results"].append({
            "details": {"contract_type": "call", "strike_price": 305.0,
                        "expiration_date": 20260708},        # int, not "2026-07-08"
            "greeks": {"delta": 0.5, "gamma": 0.02}, "implied_volatility": 0.3,
            "day": {"close": 1.0, "volume": 1, "last_updated": 1782964800000000000}})
        df = self._adapter().normalize(raw, symbol="AAPL")   # must not raise
        self.assertEqual(len(df), 30)                        # the int-expiry row dropped

    def test_wide_cluster_is_refused(self):
        # >=5 near-ATM contracts but an inconsistent (wide) implied-spot cluster must be
        # refused rather than emit a bad spot. Strikes 100..110 at delta 0.5 imply spots
        # spanning ~10% -> dispersion gate rejects.
        results = [{"details": {"contract_type": "call", "strike_price": float(k),
                                "expiration_date": "2026-08-01"},
                    "greeks": {"delta": 0.5, "gamma": 0.02}, "implied_volatility": 0.3,
                    "day": {"last_updated": 1782964800000000000}}
                   for k in range(100, 112, 2)]
        with self.assertRaises(ValueError):
            self._adapter().normalize({"results": results}, symbol="WIDE")


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestMassivePipeline(unittest.TestCase):
    def test_write_read_and_registration(self):
        import tempfile

        from src.ingest import io
        from src.ingest.adapters.massive import MassiveAdapter
        import src.ingest.adapters  # noqa: F401  (triggers registration)

        self.assertIs(get_adapter("massive"), MassiveAdapter)
        df = MassiveAdapter(api_key="test").normalize(_load_raw(), symbol="AAPL")
        qd = df["quote_ts"].iloc[0].date()
        with tempfile.TemporaryDirectory() as root:
            io.write_canonical(df, root, "AAPL", qd)
            back = io.read_canonical(root, "AAPL", qd)
        self.assertEqual(len(back), 30)
        self.assertEqual(schema.validate_frame(back), [])


if __name__ == "__main__":
    unittest.main()
