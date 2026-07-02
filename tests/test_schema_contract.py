"""Contract tests for the canonical option-chain schema.

Pure stdlib (unittest) so they run before the data stack is installed:

    python3 -m unittest discover -s tests -v

These lock the rules that keep lookahead and bad data out of the metric engine.
"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ingest import schema  # noqa: E402
from src.ingest.adapter import (  # noqa: E402
    ChainAdapter,
    get_adapter,
    register_adapter,
    registered_adapters,
)

UTC = dt.timezone.utc


def good_row(**overrides):
    """A minimal valid canonical row; override fields to build negative cases."""
    row = {
        "symbol": "SPY",
        "quote_ts": dt.datetime(2024, 6, 3, 20, 0, tzinfo=UTC),
        "expiration": dt.date(2024, 6, 21),
        "strike": 530.0,
        "type": "call",
        "underlying_price": 528.4,
        "bid": 3.1,
        "ask": 3.3,
        "last": 3.2,
        "open_interest": 12000,
        "oi_asof_date": dt.date(2024, 6, 2),
        "volume": 4200,
        "iv": 0.14,
        "delta": 0.42,
        "gamma": 0.03,
        "theta": -0.05,
        "vega": 0.11,
        "rho": 0.02,
        "_iv_source": "eodhd",
        "_greek_source": "eodhd",
        "_adapter": "eodhd",
    }
    row.update(overrides)
    return row


class TestSchemaShape(unittest.TestCase):
    def test_field_names_match_dtype_keys(self):
        self.assertEqual(schema.field_names(), list(schema.pandas_dtypes().keys()))

    def test_primary_key_fields_are_all_required(self):
        for key in schema.PRIMARY_KEY:
            self.assertIn(key, schema.REQUIRED_FIELDS, f"{key} must be non-null")

    def test_provenance_and_pit_fields_present(self):
        names = set(schema.field_names())
        for expected in ("oi_asof_date", "_adapter", "_iv_source", "_greek_source",
                         "underlying_price"):
            self.assertIn(expected, names)

    def test_partition_layout(self):
        self.assertEqual(
            schema.partition_relpath("spy", dt.date(2024, 6, 3)),
            "symbol=SPY/date=2024-06-03",
        )
        # dotted/dashed tickers are fine; path-traversal symbols are rejected (N3).
        self.assertTrue(
            schema.partition_relpath("BRK.B", dt.date(2024, 6, 3)).startswith("symbol=BRK.B"))
        with self.assertRaises(ValueError):
            schema.partition_relpath("../evil", dt.date(2024, 6, 3))


class TestValidateRecords(unittest.TestCase):
    def test_good_row_passes(self):
        self.assertEqual(schema.validate_records([good_row()]), [])

    def test_missing_required_field_fails(self):
        row = good_row()
        del row["underlying_price"]
        with self.assertRaises(schema.SchemaError):
            schema.validate_records([row])

    def test_bad_option_type_fails(self):
        issues = schema.validate_records([good_row(type="C")], raise_on_error=False)
        self.assertTrue(any("type=" in i for i in issues))

    def test_non_positive_strike_and_spot_fail(self):
        self.assertTrue(schema.validate_records([good_row(strike=0)], raise_on_error=False))
        self.assertTrue(
            schema.validate_records([good_row(underlying_price=-1.0)], raise_on_error=False))

    def test_negative_open_interest_fails(self):
        self.assertTrue(
            schema.validate_records([good_row(open_interest=-5)], raise_on_error=False))

    def test_naive_quote_ts_fails(self):
        naive = good_row(quote_ts=dt.datetime(2024, 6, 3, 20, 0))  # no tzinfo
        issues = schema.validate_records([naive], raise_on_error=False)
        self.assertTrue(any("timezone-aware" in i for i in issues))

    def test_expiration_before_quote_is_lookahead(self):
        row = good_row(expiration=dt.date(2024, 6, 1))  # before quote date 2024-06-03
        issues = schema.validate_records([row], raise_on_error=False)
        self.assertTrue(any("precedes quote date" in i for i in issues))

    def test_same_day_expiration_allowed_0dte(self):
        row = good_row(expiration=dt.date(2024, 6, 3))  # 0DTE == quote date
        self.assertEqual(schema.validate_records([row]), [])

    def test_oi_asof_after_quote_is_lookahead(self):
        row = good_row(oi_asof_date=dt.date(2024, 6, 4))  # after quote date
        issues = schema.validate_records([row], raise_on_error=False)
        self.assertTrue(any("open-interest lookahead" in i for i in issues))

    def test_nullable_fields_may_be_none(self):
        row = good_row(bid=None, ask=None, last=None, iv=None, delta=None,
                       open_interest=None, oi_asof_date=None, volume=None,
                       _iv_source=None, _greek_source=None)
        self.assertEqual(schema.validate_records([row]), [])

    def test_unknown_field_flagged(self):
        issues = schema.validate_records([good_row(surprise=1)], raise_on_error=False)
        self.assertTrue(any("unknown field 'surprise'" in i for i in issues))

    def test_duplicate_primary_key_rejected(self):
        # F5: two rows with the same (symbol, quote_ts, expiration, strike, type).
        issues = schema.validate_records([good_row(), good_row()], raise_on_error=False)
        self.assertTrue(any("duplicate primary key" in i for i in issues))

    def test_same_strike_different_type_is_not_duplicate(self):
        call = good_row(type="call")
        put = good_row(type="put", delta=-0.4)
        self.assertEqual(schema.validate_records([call, put]), [])

    def test_iso_string_timestamps_accepted(self):
        row = good_row(
            quote_ts="2024-06-03T20:00:00+00:00",
            expiration="2024-06-21",
            oi_asof_date="2024-06-02",
        )
        self.assertEqual(schema.validate_records([row]), [])


class TestAdapterInterface(unittest.TestCase):
    def test_cannot_instantiate_abstract(self):
        with self.assertRaises(TypeError):
            ChainAdapter()  # abstract methods unimplemented

    def test_registry_roundtrip(self):
        @register_adapter
        class _FakeAdapter(ChainAdapter):
            name = "_fake_test"

            def fetch_raw(self, symbol, quote_date, **kwargs):
                return None

            def normalize(self, raw, *, symbol, quote_date):
                raise NotImplementedError

        self.assertIn("_fake_test", registered_adapters())
        self.assertIs(get_adapter("_fake_test"), _FakeAdapter)

    def test_unknown_adapter_raises(self):
        with self.assertRaises(KeyError):
            get_adapter("does-not-exist")

    def test_register_requires_name(self):
        with self.assertRaises(ValueError):
            @register_adapter
            class _Nameless(ChainAdapter):
                def fetch_raw(self, symbol, quote_date, **kwargs):
                    return None

                def normalize(self, raw, *, symbol, quote_date):
                    return None


if __name__ == "__main__":
    unittest.main()
