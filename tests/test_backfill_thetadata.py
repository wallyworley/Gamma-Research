"""Backfill runner tests (scripts/backfill_thetadata.py): resumability + no-data skips.

No live HTTP: a fake adapter stands in for ThetadataAdapter, so the resume gate
(skip-existing partitions), the clean no-data skip, and failure isolation are exercised
deterministically against a temp store. Needs the data stack (the runner writes parquet);
skipped without it so the stdlib CI leg still collects this module.
"""

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

try:
    import pandas as pd  # noqa: F401
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "thetadata_sample.json")

# Two adjacent NYSE trading days used across the resume/no-data tests.
_A = dt.date(2026, 6, 30)   # Tuesday
_B = dt.date(2026, 7, 1)    # Wednesday


def _canonical_df():
    """A valid canonical frame (the recorded fixture, normalized) to write/return."""
    from src.ingest.adapters.thetadata import ThetadataAdapter
    with open(_FIXTURE) as fh:
        raw = json.load(fh)
    return ThetadataAdapter(api_key="test").normalize(raw, symbol="AAPL")


class _FakeAdapter:
    """Records load() calls; can raise NoDataForSession or a generic error per session."""

    def __init__(self, df, *, no_data=(), fail=()):
        self._df = df
        self._no_data = set(no_data)
        self._fail = set(fail)
        self.calls = []
        self._lock = threading.Lock()

    def load(self, symbol, quote_date):
        from src.ingest.adapters.thetadata import NoDataForSession
        with self._lock:
            self.calls.append((symbol, quote_date))
        if quote_date in self._no_data:
            raise NoDataForSession(f"no data for {quote_date}")
        if quote_date in self._fail:
            raise ValueError("boom")
        return self._df


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestBackfillRunner(unittest.TestCase):
    def setUp(self):
        self.df = _canonical_df()

    def test_skip_existing_is_resumable(self):
        # Pre-write session A; running over [A, B] must fetch ONLY B (A already stored).
        import backfill_thetadata as bf
        from src.ingest import io

        with tempfile.TemporaryDirectory() as root:
            io.write_canonical(self.df, root, "AAPL", _A)      # A already in the store
            fake = _FakeAdapter(self.df)
            counts = bf.run_backfill(["AAPL"], _A, _B, root, adapter=fake, workers=1)

            self.assertEqual(fake.calls, [("AAPL", _B)])       # only the missing session
            self.assertEqual(counts["skipped_existing"], 1)
            self.assertEqual(counts["written"], 1)
            self.assertEqual(counts["no_data"], 0)
            self.assertEqual(counts["failed"], 0)
            # B is now stored, so a second run is a pure no-op (fully resumable).
            fake2 = _FakeAdapter(self.df)
            counts2 = bf.run_backfill(["AAPL"], _A, _B, root, adapter=fake2, workers=1)
            self.assertEqual(fake2.calls, [])
            self.assertEqual(counts2["skipped_existing"], 2)
            self.assertEqual(counts2["written"], 0)

    def test_no_data_session_counted_as_skip_not_failure(self):
        import backfill_thetadata as bf

        with tempfile.TemporaryDirectory() as root:
            fake = _FakeAdapter(self.df, no_data={_A})
            counts = bf.run_backfill(["AAPL"], _A, _A, root, adapter=fake, workers=1)
            self.assertEqual(counts["no_data"], 1)
            self.assertEqual(counts["failed"], 0)
            self.assertEqual(counts["written"], 0)
            # No partition written for a no-data session.
            self.assertFalse(os.path.isdir(os.path.join(root, "symbol=AAPL")))

    def test_failure_is_isolated_batch_continues(self):
        # One session errors; the other still writes. The failure is counted, not fatal.
        import backfill_thetadata as bf

        with tempfile.TemporaryDirectory() as root:
            fake = _FakeAdapter(self.df, fail={_A})
            counts = bf.run_backfill(["AAPL"], _A, _B, root, adapter=fake, workers=1)
            self.assertEqual(counts["failed"], 1)
            self.assertEqual(counts["written"], 1)
            from src.ingest import io
            back = io.read_canonical(root, "AAPL", _B)
            self.assertEqual(len(back), len(self.df))

    def test_only_trading_days_attempted(self):
        # The range spans a weekend (Sat 7/4 is also the observed-holiday-adjacent stretch):
        # only NYSE sessions are attempted, weekends never reach the adapter.
        import backfill_thetadata as bf

        with tempfile.TemporaryDirectory() as root:
            fake = _FakeAdapter(self.df)
            # Fri 2026-07-03 is the observed July 4 holiday; Sat/Sun 7/4-7/5 are weekends.
            bf.run_backfill(["AAPL"], dt.date(2026, 7, 2), dt.date(2026, 7, 6),
                            root, adapter=fake, workers=1)
            attempted = {d for _, d in fake.calls}
            self.assertIn(dt.date(2026, 7, 2), attempted)     # Thursday
            self.assertIn(dt.date(2026, 7, 6), attempted)     # Monday
            self.assertNotIn(dt.date(2026, 7, 3), attempted)  # observed holiday
            self.assertNotIn(dt.date(2026, 7, 4), attempted)  # Saturday
            self.assertNotIn(dt.date(2026, 7, 5), attempted)  # Sunday

    def test_status_heartbeat_written(self):
        import backfill_thetadata as bf

        with tempfile.TemporaryDirectory() as root:
            fake = _FakeAdapter(self.df)
            bf.run_backfill(["AAPL"], _A, _A, root, adapter=fake, workers=1)
            status = os.path.join(root, ".backfill_status.json")
            self.assertTrue(os.path.exists(status))
            payload = json.loads(Path(status).read_text())
            self.assertTrue(payload["done"])
            self.assertEqual(payload["written"], 1)

    def test_concurrent_workers_isolate_and_count(self):
        # Threaded branch (workers=2): a raising session must not abort the batch and every
        # session is accounted for exactly once.
        import backfill_thetadata as bf

        with tempfile.TemporaryDirectory() as root:
            fake = _FakeAdapter(self.df, fail={_A})
            counts = bf.run_backfill(["AAPL"], _A, _B, root, adapter=fake, workers=2)
            self.assertEqual(counts["written"] + counts["failed"], 2)
            self.assertEqual(counts["written"], 1)
            self.assertEqual(counts["failed"], 1)


if __name__ == "__main__":
    unittest.main()
