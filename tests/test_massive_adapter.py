"""MassiveAdapter tests: normalize mapping against a recorded real fixture.

Live HTTP (fetch_raw) is integration-tested manually with a key; parsing is tested
against a recorded snapshot. Needs the data stack; skipped without it.
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

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "massive_options_sample.json")


def _load_raw():
    with open(_FIXTURE) as fh:
        return json.load(fh)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestMassiveNormalize(unittest.TestCase):
    def setUp(self):
        from src.ingest.adapters.massive import MassiveAdapter
        self.df = MassiveAdapter(api_key="test").normalize(_load_raw(), symbol="AAPL")

    def test_output_is_canonical_and_valid(self):
        self.assertEqual(list(self.df.columns), schema.field_names())
        self.assertEqual(len(self.df), 6)
        self.assertEqual(schema.validate_frame(self.df), [])

    def test_types_and_spot_and_provenance(self):
        self.assertEqual(set(self.df["type"]), {"call", "put"})
        self.assertTrue((self.df["underlying_price"] == 294.38).all())
        for col in ("_adapter", "_greek_source", "_iv_source"):
            self.assertTrue((self.df[col] == "massive").all())

    def test_rho_is_null(self):
        # Massive greeks carry no rho; the column must be entirely null.
        self.assertTrue(self.df["rho"].isna().all())

    def test_quote_ts_anchored_to_session_close(self):
        # session_date 2026-07-01 -> quote_ts 16:00 EDT = 20:00 UTC.
        ts = self.df["quote_ts"].iloc[0]
        self.assertEqual(str(ts.tz), "UTC")
        self.assertEqual((ts.year, ts.month, ts.day, ts.hour), (2026, 7, 1, 20))

    def test_oi_asof_prior_weekday(self):
        oa = self.df["oi_asof_date"].iloc[0].date()
        self.assertLess(oa, dt.date(2026, 7, 1))
        self.assertLess(oa.weekday(), 5)

    def test_field_mapping(self):
        # Deep ITM call: real OI, but Massive leaves IV unset -> null (not a faked 0).
        deep = self.df[(self.df["type"] == "call") & (self.df["strike"] == 295.0)
                       & (self.df["expiration"].dt.date == dt.date(2026, 7, 2))].iloc[0]
        self.assertEqual(int(deep["open_interest"]), 13575)
        self.assertTrue(pd.isna(deep["iv"]))
        self.assertTrue(pd.isna(deep["bid"]))            # bid/ask not entitled on tier
        # A contract with real greeks/IV maps through.
        put = self.df[(self.df["type"] == "put") & (self.df["strike"] == 295.0)
                      & (self.df["expiration"].dt.date == dt.date(2026, 7, 2))].iloc[0]
        self.assertGreater(float(put["iv"]), 2.0)
        self.assertGreater(float(put["gamma"]), 0.0)

    def test_missing_spot_raises(self):
        from src.ingest.adapters.massive import MassiveAdapter
        raw = _load_raw()
        raw["underlying_close"] = None
        with self.assertRaises(ValueError):
            MassiveAdapter(api_key="test").normalize(raw, symbol="AAPL")

    def test_all_malformed_raises(self):
        from src.ingest.adapters.massive import MassiveAdapter
        raw = {"underlying_close": 100.0, "session_date": "2026-07-01",
               "results": [{"details": {}}]}   # no strike/type/expiration
        with self.assertRaises(ValueError):
            MassiveAdapter(api_key="test").normalize(raw, symbol="AAPL")


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
        self.assertEqual(len(back), 6)
        self.assertEqual(schema.validate_frame(back), [])


if __name__ == "__main__":
    unittest.main()
