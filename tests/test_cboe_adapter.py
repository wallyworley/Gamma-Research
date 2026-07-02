"""CboeAdapter tests: OSI/timestamp parsing, normalize mapping, validation.

Live HTTP (fetch_raw) is not covered here; all parsing is tested against a
recorded real fixture. Needs the data stack; skipped without it.

    .venv/bin/python -m unittest discover -s tests -v
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

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "cboe_options_sample.json")


def _load_raw():
    with open(_FIXTURE) as fh:
        return json.load(fh)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestCboeParsers(unittest.TestCase):
    def test_parse_osi_call_and_put(self):
        from src.ingest.adapters.cboe import _parse_osi
        self.assertEqual(_parse_osi("AAPL260702C00307500"), (dt.date(2026, 7, 2), 307.5, "call", "AAPL"))
        self.assertEqual(_parse_osi("AAPL260710P00110000"), (dt.date(2026, 7, 10), 110.0, "put", "AAPL"))

    def test_parse_osi_weekly_index_root(self):
        from src.ingest.adapters.cboe import _parse_osi
        # The root is captured (SPXW vs SPX) so AM/PM variants can be distinguished.
        self.assertEqual(_parse_osi("SPXW260702P05000000"), (dt.date(2026, 7, 2), 5000.0, "put", "SPXW"))

    def test_parse_osi_rejects_junk(self):
        from src.ingest.adapters.cboe import _parse_osi
        self.assertIsNone(_parse_osi("NOTANOPTION"))
        self.assertIsNone(_parse_osi(""))

    def test_parse_ts_is_utc(self):
        from src.ingest.adapters.cboe import _parse_ts
        ts = _parse_ts("2026-07-02 21:19:32")
        self.assertEqual(ts.utcoffset(), dt.timedelta(0))
        self.assertEqual((ts.year, ts.month, ts.day, ts.hour), (2026, 7, 2, 21))


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestCboeNormalize(unittest.TestCase):
    def setUp(self):
        from src.ingest.adapters.cboe import CboeAdapter
        self.df = CboeAdapter().normalize(_load_raw(), symbol="AAPL")

    def test_output_is_canonical_and_valid(self):
        self.assertEqual(list(self.df.columns), schema.field_names())
        self.assertEqual(len(self.df), 6)
        self.assertEqual(schema.validate_frame(self.df), [])

    def test_quote_ts_anchored_to_session_close(self):
        # Fixture timestamp 2026-07-02 21:19:32 UTC -> ET session 2026-07-02 ->
        # quote_ts = 16:00 EDT = 20:00 UTC (anchored to the close, B1).
        ts = self.df["quote_ts"].iloc[0]
        self.assertEqual(str(ts.tz), "UTC")
        self.assertEqual((ts.year, ts.month, ts.day, ts.hour), (2026, 7, 2, 20))

    def test_spot_and_provenance(self):
        self.assertTrue((self.df["underlying_price"] == 308.0).all())
        for col in ("_adapter", "_greek_source", "_iv_source"):
            self.assertTrue((self.df[col] == "cboe").all())

    def test_oi_asof_prior_weekday(self):
        q = self.df["quote_ts"].iloc[0].date()
        oa = self.df["oi_asof_date"].iloc[0].date()
        self.assertLess(oa, q)
        self.assertLess(oa.weekday(), 5)  # a weekday

    def test_type_and_field_mapping(self):
        self.assertEqual(set(self.df["type"]), {"call", "put"})
        row = self.df[(self.df["type"] == "call") & (self.df["strike"] == 307.5)].iloc[0]
        self.assertAlmostEqual(float(row["gamma"]), 0.252)
        self.assertEqual(int(row["open_interest"]), 2474)
        self.assertAlmostEqual(float(row["iv"]), 0.4595)

    def test_zero_iv_gamma_row_validates(self):
        # deep-ITM call (strike 110, iv=0, gamma=0, delta=1) must pass validation.
        row = self.df[(self.df["type"] == "call") & (self.df["strike"] == 110.0)].iloc[0]
        self.assertEqual(float(row["iv"]), 0.0)
        self.assertEqual(float(row["gamma"]), 0.0)

    def test_two_expirations(self):
        self.assertEqual(set(self.df["expiration"].dt.date),
                         {dt.date(2026, 7, 2), dt.date(2026, 7, 10)})

    def test_missing_spot_raises(self):
        from src.ingest.adapters.cboe import CboeAdapter
        raw = _load_raw()
        raw["data"]["current_price"] = None
        with self.assertRaises(ValueError):
            CboeAdapter().normalize(raw, symbol="AAPL")

    def test_evening_utc_rollover_uses_et_session(self):
        # B1: a payload generated at 00:30 UTC (8:30pm ET, still session 2026-07-02)
        # must resolve to the ET session date, not roll to 07-03. Previously the
        # whole 0DTE chain would be rejected as "expiration precedes quote date".
        from src.ingest.adapters.cboe import CboeAdapter
        raw = _load_raw()
        raw["timestamp"] = "2026-07-03 00:30:00"    # = 2026-07-02 20:30 ET
        df = CboeAdapter().normalize(raw, symbol="AAPL")
        self.assertEqual(df["quote_ts"].iloc[0].date(), dt.date(2026, 7, 2))
        self.assertEqual(schema.validate_frame(df), [])
        self.assertEqual(len(df), 6)

    def test_index_dual_settlement_raises(self):
        # B2: SPX (AM-settled) and SPXW (PM-settled) at the same expiration/strike/
        # type are distinct contracts; merging would silently drop OI, so raise.
        from src.ingest.adapters.cboe import CboeAdapter
        raw = _load_raw()
        raw["data"]["current_price"] = 5000.0
        raw["data"]["options"] = [
            {"option": "SPX260717C05000000", "open_interest": 748, "iv": 0.2,
             "gamma": 0.01, "delta": 0.5, "bid": 1, "ask": 2, "last_trade_price": 1.5},
            {"option": "SPXW260717C05000000", "open_interest": 5071, "iv": 0.2,
             "gamma": 0.01, "delta": 0.5, "bid": 1, "ask": 2, "last_trade_price": 1.5},
        ]
        with self.assertRaises(NotImplementedError):
            CboeAdapter(index=True).normalize(raw, symbol="SPX")


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestCboeToParquet(unittest.TestCase):
    def test_full_pipeline_normalize_write_read(self):
        import tempfile

        from src.ingest import io
        from src.ingest.adapters.cboe import CboeAdapter

        df = CboeAdapter().normalize(_load_raw(), symbol="AAPL")
        qd = df["quote_ts"].iloc[0].date()
        with tempfile.TemporaryDirectory() as root:
            io.write_canonical(df, root, "AAPL", qd)
            back = io.read_canonical(root, "AAPL", qd)
        self.assertEqual(len(back), 6)
        self.assertEqual(schema.validate_frame(back), [])
        self.assertTrue((back["_adapter"] == "cboe").all())


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestCboeRegistrationAndUrl(unittest.TestCase):
    def test_registered_under_name(self):
        import src.ingest.adapters  # noqa: F401  (import triggers registration)
        from src.ingest.adapters.cboe import CboeAdapter
        self.assertIs(get_adapter("cboe"), CboeAdapter)

    def test_url_building(self):
        from src.ingest.adapters.cboe import CboeAdapter
        base = "https://cdn.cboe.com/api/global/delayed_quotes/options"
        self.assertEqual(CboeAdapter()._url("aapl"), f"{base}/AAPL.json")
        self.assertEqual(CboeAdapter(index=True)._url("SPX"), f"{base}/_SPX.json")

    def test_duplicate_contracts_deduped(self):
        from src.ingest.adapters.cboe import CboeAdapter
        raw = _load_raw()
        opts = raw["data"]["options"]
        raw["data"]["options"] = opts + opts  # double every contract
        df = CboeAdapter().normalize(raw, symbol="AAPL")
        self.assertEqual(len(df), 6)


if __name__ == "__main__":
    unittest.main()
