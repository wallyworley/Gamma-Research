"""Parquet round-trip + frame validation tests.

These need the data stack (pandas/pyarrow) and are skipped when it is absent, so
the stdlib contract tests still run on a bare interpreter. With the stack
installed they exercise src/ingest/io.py and schema.validate_frame end-to-end:

    .venv/bin/python -m unittest discover -s tests -v
"""

import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pandas as pd  # noqa: F401
    import pyarrow  # noqa: F401
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False

from src.ingest import schema  # noqa: E402

UTC = dt.timezone.utc


def canonical_frame():
    """A tiny 2-row (call/put) canonical frame with correct dtypes."""
    import pandas as pd

    rows = [
        {
            "symbol": "SPY", "root": "SPY", "quote_ts": pd.Timestamp("2024-06-03 20:00", tz="UTC"),
            "expiration": pd.Timestamp("2024-06-21"), "strike": 530.0, "type": "call",
            "underlying_price": 528.4, "bid": 3.1, "ask": 3.3, "last": 3.2,
            "open_interest": 12000, "oi_asof_date": pd.Timestamp("2024-06-02"),
            "volume": 4200, "iv": 0.14, "delta": 0.42, "gamma": 0.03, "theta": -0.05,
            "vega": 0.11, "rho": 0.02, "_iv_source": "eodhd", "_greek_source": "eodhd",
            "_adapter": "eodhd",
        },
        {
            "symbol": "SPY", "root": "SPY", "quote_ts": pd.Timestamp("2024-06-03 20:00", tz="UTC"),
            "expiration": pd.Timestamp("2024-06-21"), "strike": 525.0, "type": "put",
            "underlying_price": 528.4, "bid": 2.0, "ask": 2.2, "last": 2.1,
            "open_interest": 8000, "oi_asof_date": pd.Timestamp("2024-06-02"),
            "volume": 1500, "iv": 0.15, "delta": -0.35, "gamma": 0.028, "theta": -0.04,
            "vega": 0.10, "rho": -0.02, "_iv_source": "eodhd", "_greek_source": "eodhd",
            "_adapter": "eodhd",
        },
    ]
    df = pd.DataFrame(rows, columns=schema.field_names())
    return df.astype(schema.pandas_dtypes())


def session_frame(session_date, open_interest):
    """A tiny 1-row canonical frame for one SPY session (distinct OI per session)."""
    import pandas as pd

    row = {
        "symbol": "SPY", "root": "SPY",
        "quote_ts": pd.Timestamp(f"{session_date} 20:00", tz="UTC"),
        "expiration": pd.Timestamp("2024-06-21"), "strike": 530.0, "type": "call",
        "underlying_price": 528.4, "bid": 3.1, "ask": 3.3, "last": 3.2,
        "open_interest": open_interest, "oi_asof_date": pd.Timestamp("2024-05-31"),
        "volume": 4200, "iv": 0.14, "delta": 0.42, "gamma": 0.03, "theta": -0.05,
        "vega": 0.11, "rho": 0.02, "_iv_source": "eodhd", "_greek_source": "eodhd",
        "_adapter": "eodhd", "_spot_source": "vendor_close",
    }
    df = pd.DataFrame([row], columns=schema.field_names())
    return df.astype(schema.pandas_dtypes())


@unittest.skipUnless(_HAVE_STACK, "pandas/pyarrow not installed")
class TestFrameValidation(unittest.TestCase):
    def test_good_frame_passes(self):
        self.assertEqual(schema.validate_frame(canonical_frame()), [])

    def test_lookahead_frame_rejected(self):
        df = canonical_frame()
        df.loc[0, "oi_asof_date"] = pd.Timestamp("2024-06-04")  # after quote date
        with self.assertRaises(schema.SchemaError):
            schema.validate_frame(df)

    def test_missing_required_column_rejected(self):
        df = canonical_frame().drop(columns=["underlying_price"])
        with self.assertRaises(schema.SchemaError):
            schema.validate_frame(df)


@unittest.skipUnless(_HAVE_STACK, "pandas/pyarrow not installed")
class TestParquetRoundTrip(unittest.TestCase):
    def test_write_then_read_matches(self):
        from src.ingest import io

        df = canonical_frame()
        with tempfile.TemporaryDirectory() as root:
            path = io.write_canonical(df, root, "SPY", dt.date(2024, 6, 3))
            self.assertTrue(os.path.exists(path))
            self.assertIn("symbol=SPY", path)
            self.assertIn("date=2024-06-03", path)
            back = io.read_canonical(root, "SPY", dt.date(2024, 6, 3))

        self.assertEqual(list(back.columns), schema.field_names())
        self.assertEqual(len(back), 2)
        self.assertEqual(set(back["type"]), {"call", "put"})
        self.assertEqual(schema.validate_frame(back), [])
        # Canonical dtypes on read: parquet date32 must come back as datetime64 (not
        # object), else the metric engine's `.dt` horizon math breaks on stored data.
        self.assertEqual(str(back["expiration"].dtype), "datetime64[ns]")
        self.assertEqual(str(back["oi_asof_date"].dtype), "datetime64[ns]")
        self.assertEqual(dict(back.dtypes.astype(str)), schema.pandas_dtypes())

    def test_read_backfills_missing_nullable_column(self):
        # Schema evolution: a partition written before _spot_source existed lacks the
        # column; read_canonical must backfill it as null so old data still validates.
        import pyarrow as pa
        import pyarrow.parquet as pq

        from src.ingest import io

        df = canonical_frame().drop(columns=["_spot_source"])   # pre-_spot_source layout
        with tempfile.TemporaryDirectory() as root:
            part = os.path.join(root, schema.partition_relpath("SPY", dt.date(2024, 6, 3)))
            os.makedirs(part, exist_ok=True)
            pq.write_table(pa.Table.from_pandas(df, preserve_index=False),
                           os.path.join(part, "chain.parquet"))
            back = io.read_canonical(root, "SPY", dt.date(2024, 6, 3))
        self.assertIn("_spot_source", back.columns)
        self.assertTrue(back["_spot_source"].isna().all())
        self.assertEqual(schema.validate_frame(back), [])

    def _write_three_sessions(self, root):
        from src.ingest import io

        # Written out of order (06-05 first) to prove the reader sorts, not the writer.
        for d, oi in ((dt.date(2024, 6, 5), 5000),
                      (dt.date(2024, 6, 3), 3000),
                      (dt.date(2024, 6, 4), 4000)):
            io.write_canonical(session_frame(d.isoformat(), oi), root, "SPY", d)

    def test_iter_partitions_lists_matches_in_order(self):
        from src.ingest import io

        with tempfile.TemporaryDirectory() as root:
            self._write_three_sessions(root)
            # An unrelated symbol/session must not leak into a SPY scan.
            io.write_canonical(session_frame("2024-06-04", 9), root, "QQQ", dt.date(2024, 6, 4))

            got = list(io.iter_partitions(root, symbol="SPY"))
            self.assertEqual([d for _sym, d, _p in got],
                             [dt.date(2024, 6, 3), dt.date(2024, 6, 4), dt.date(2024, 6, 5)])
            self.assertTrue(all(sym == "SPY" for sym, _d, _p in got))
            self.assertTrue(all(p.endswith("chain.parquet") for _s, _d, p in got))

            # Date-range filter (inclusive) narrows the scan.
            ranged = list(io.iter_partitions(root, symbol="SPY",
                                             start=dt.date(2024, 6, 4), end="2024-06-05"))
            self.assertEqual([d for _s, d, _p in ranged],
                             [dt.date(2024, 6, 4), dt.date(2024, 6, 5)])

            # Both symbols visible when unfiltered.
            self.assertEqual({sym for sym, _d, _p in io.iter_partitions(root)}, {"SPY", "QQQ"})

    def test_read_symbol_history_concatenates_sorted(self):
        from src.ingest import io

        with tempfile.TemporaryDirectory() as root:
            self._write_three_sessions(root)
            hist = io.read_symbol_history(root, "SPY")

        self.assertEqual(len(hist), 3)
        self.assertEqual(list(hist.columns), schema.field_names())
        # Ordered by quote_ts ascending regardless of write order (OI encodes the day).
        self.assertEqual(list(hist["open_interest"]), [3000, 4000, 5000])
        self.assertTrue(hist["quote_ts"].is_monotonic_increasing)
        self.assertEqual(schema.validate_frame(hist), [])

    def test_read_symbol_history_range_and_empty(self):
        from src.ingest import io

        with tempfile.TemporaryDirectory() as root:
            self._write_three_sessions(root)
            mid = io.read_symbol_history(root, "SPY", start="2024-06-04")
            self.assertEqual(list(mid["open_interest"]), [4000, 5000])

            # No matching partition -> an empty, canonically-typed frame (not an error).
            empty = io.read_symbol_history(root, "NOPE")
        self.assertEqual(len(empty), 0)
        self.assertEqual(list(empty.columns), schema.field_names())
        self.assertEqual(dict(empty.dtypes.astype(str)), schema.pandas_dtypes())

    def test_read_backfills_root_from_symbol_on_legacy_partition(self):
        # A partition written before `root` (a REQUIRED key field) existed: read_canonical
        # must derive root from symbol (equities: root == ticker) so it stays readable.
        import pyarrow as pa
        import pyarrow.parquet as pq

        from src.ingest import io

        df = canonical_frame().drop(columns=["root"])   # pre-root layout
        with tempfile.TemporaryDirectory() as root:
            part = os.path.join(root, schema.partition_relpath("SPY", dt.date(2024, 6, 3)))
            os.makedirs(part, exist_ok=True)
            pq.write_table(pa.Table.from_pandas(df, preserve_index=False),
                           os.path.join(part, "chain.parquet"))
            back = io.read_canonical(root, "SPY", dt.date(2024, 6, 3))
        self.assertTrue((back["root"] == back["symbol"]).all())
        self.assertEqual(schema.validate_frame(back), [])


if __name__ == "__main__":
    unittest.main()
