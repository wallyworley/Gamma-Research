"""EODHD adapter tests: normalize mapping + canonical validation.

Exercise the pure mapping (normalize / _extract_records) against a recorded
JSON:API fixture. Live HTTP (fetch_raw) is not covered here; it needs a real
token and is integration-tested manually. Needs the data stack; skipped without.

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

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "eodhd_options_eod_sample.json")
_QUOTE_DATE = dt.date(2024, 6, 3)
_SPOT = 194.03


def _load_raw():
    with open(_FIXTURE) as fh:
        page = json.load(fh)
    from src.ingest.adapters.eodhd import _extract_records
    return {"records": _extract_records(page), "underlying_close": _SPOT}


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestEodhdNormalize(unittest.TestCase):
    def setUp(self):
        from src.ingest.adapters.eodhd import EodhdAdapter
        self.adapter = EodhdAdapter(api_token="test")
        self.df = self.adapter.normalize(_load_raw(), symbol="AAPL", quote_date=_QUOTE_DATE)

    def test_output_is_canonical_and_valid(self):
        self.assertEqual(list(self.df.columns), schema.field_names())
        self.assertEqual(len(self.df), 4)
        self.assertEqual(schema.validate_frame(self.df), [])

    def test_types_lowercased(self):
        self.assertEqual(set(self.df["type"]), {"call", "put"})

    def test_spot_attached_to_every_row(self):
        self.assertTrue((self.df["underlying_price"] == _SPOT).all())

    def test_quote_ts_is_equity_close_utc(self):
        # June => EDT (UTC-4), so 16:00 ET == 20:00 UTC.
        ts = self.df["quote_ts"].iloc[0]
        self.assertEqual(str(ts.tz), "UTC")
        self.assertEqual((ts.hour, ts.minute), (20, 0))
        self.assertEqual(ts.date(), _QUOTE_DATE)

    def test_provenance_stamped(self):
        for col in ("_adapter", "_greek_source", "_iv_source"):
            self.assertTrue((self.df[col] == "eodhd").all())

    def test_oi_asof_stamped_prior_weekday(self):
        # F1: OI as-of is stamped (default T-1 weekday), not left null.
        # quote_date 2024-06-03 (Mon) -> prior weekday 2024-05-31 (Fri).
        self.assertTrue((self.df["oi_asof_date"].dt.date == dt.date(2024, 5, 31)).all())
        self.assertTrue((self.df["oi_asof_date"].dt.date <= _QUOTE_DATE).all())

    def test_oi_lag_zero_uses_quote_date(self):
        from src.ingest.adapters.eodhd import EodhdAdapter
        df = EodhdAdapter(api_token="test", oi_lag_days=0).normalize(
            _load_raw(), symbol="AAPL", quote_date=_QUOTE_DATE)
        self.assertTrue((df["oi_asof_date"].dt.date == _QUOTE_DATE).all())

    def test_oi_asof_weekday_not_holiday_aware(self):
        # F1: the stamp is a weekday lag. Weekend-skip is correct...
        from src.ingest.adapters.eodhd import EodhdAdapter
        a = EodhdAdapter(api_token="test")
        self.assertEqual(a._oi_asof_date(dt.date(2024, 6, 3)), dt.date(2024, 5, 31))  # Mon->Fri
        # ...but it is NOT holiday-aware (known, documented limitation): a lag across
        # Independence Day names a non-session date rather than skipping it.
        self.assertEqual(a._oi_asof_date(dt.date(2024, 7, 5)), dt.date(2024, 7, 4))

    def test_duplicate_contracts_deduped(self):
        # F5: doubled records must dedupe to unique contracts, not double-count.
        from src.ingest.adapters.eodhd import EodhdAdapter
        raw = _load_raw()
        doubled = {"records": raw["records"] + raw["records"], "underlying_close": _SPOT}
        df = EodhdAdapter(api_token="test").normalize(doubled, symbol="AAPL", quote_date=_QUOTE_DATE)
        self.assertEqual(len(df), 4)
        self.assertEqual(schema.validate_frame(df), [])

    def test_put_delta_sign_preserved(self):
        puts = self.df[self.df["type"] == "put"]
        self.assertTrue((puts["delta"] <= 0).all())

    def test_liquid_call_values_mapped(self):
        row = self.df[(self.df["type"] == "call") & (self.df["strike"] == 190.0)].iloc[0]
        self.assertEqual(row["open_interest"], 15000)
        self.assertAlmostEqual(float(row["gamma"]), 0.028)
        self.assertAlmostEqual(float(row["iv"]), 0.24)

    def test_zero_greek_illiquid_row_passes(self):
        # The 2024-07-19 185 put has all-zero greeks/volume/OI; must still validate.
        row = self.df[self.df["strike"] == 185.0].iloc[0]
        self.assertEqual(row["open_interest"], 0)
        self.assertEqual(float(row["gamma"]), 0.0)

    def test_two_expirations_present(self):
        exps = set(self.df["expiration"].dt.date)
        self.assertEqual(exps, {dt.date(2024, 6, 21), dt.date(2024, 7, 19)})

    def test_missing_underlying_close_raises(self):
        raw = _load_raw()
        raw["underlying_close"] = None
        with self.assertRaises(ValueError):
            self.adapter.normalize(raw, symbol="AAPL", quote_date=_QUOTE_DATE)


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestEodhdToParquet(unittest.TestCase):
    def test_full_pipeline_normalize_write_read(self):
        import tempfile

        from src.ingest import io
        from src.ingest.adapters.eodhd import EodhdAdapter

        df = EodhdAdapter(api_token="test").normalize(
            _load_raw(), symbol="AAPL", quote_date=_QUOTE_DATE)
        with tempfile.TemporaryDirectory() as root:
            io.write_canonical(df, root, "AAPL", _QUOTE_DATE)
            back = io.read_canonical(root, "AAPL", _QUOTE_DATE)

        self.assertEqual(len(back), 4)
        self.assertEqual(list(back.columns), schema.field_names())
        self.assertEqual(schema.validate_frame(back), [])
        self.assertTrue((back["_adapter"] == "eodhd").all())


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestEodhdRegistration(unittest.TestCase):
    def test_registered_under_name(self):
        import src.ingest.adapters  # noqa: F401  (import triggers registration)
        from src.ingest.adapters.eodhd import EodhdAdapter
        self.assertIs(get_adapter("eodhd"), EodhdAdapter)

    def test_extract_records_count(self):
        from src.ingest.adapters.eodhd import _extract_records
        with open(_FIXTURE) as fh:
            page = json.load(fh)
        self.assertEqual(len(_extract_records(page)), 4)


if __name__ == "__main__":
    unittest.main()
