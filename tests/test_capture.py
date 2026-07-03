"""Tests for the snapshot capture (src/ingest/capture.py). Needs the data stack.

Uses a fake adapter (no live HTTP): the frame comes from normalizing the recorded
Cboe fixture, so the write/read partition path is exercised deterministically.
"""

import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pandas as pd  # noqa: F401
    _HAVE_STACK = True
except ImportError:
    _HAVE_STACK = False

_CBOE_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "cboe_options_sample.json")


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestCapture(unittest.TestCase):
    def _fixture_frame(self):
        from src.ingest.adapters.cboe import CboeAdapter
        with open(_CBOE_FIXTURE) as fh:
            raw = json.load(fh)
        return CboeAdapter().normalize(raw, symbol="AAPL")

    def test_capture_snapshot_writes_and_reads(self):
        from src.ingest import io, schema
        from src.ingest.capture import capture_snapshot

        df = self._fixture_frame()

        class Fake:
            def load(self, symbol):
                return df

        with tempfile.TemporaryDirectory() as root:
            res = capture_snapshot(Fake(), "AAPL", root)
            self.assertTrue(res["ok"])
            self.assertEqual(res["contracts"], 6)
            # Session dated from the snapshot (fixture's frozen 2026-07-02), not today.
            self.assertEqual(res["session"], "2026-07-02")
            back = io.read_canonical(root, "AAPL", res["session"])
            self.assertEqual(len(back), 6)
            self.assertEqual(schema.validate_frame(back), [])
            # Idempotent: re-running the session overwrites (still 6 rows, one file).
            capture_snapshot(Fake(), "AAPL", root)
            again = io.read_canonical(root, "AAPL", res["session"])
            self.assertEqual(len(again), 6)

    def test_capture_many_isolates_failures(self):
        from src.ingest.capture import capture_many

        good = self._fixture_frame()

        class Mixed:
            def load(self, symbol):
                if symbol == "BAD":
                    raise ValueError("boom")
                return good

        # today == the fixture's session (2026-07-02) so the good symbol passes the
        # session guard; BAD raises and is isolated.
        with tempfile.TemporaryDirectory() as root:
            res = capture_many(["AAPL", "BAD"], root, adapter=Mixed(), today=dt.date(2026, 7, 2))
        self.assertTrue(res["AAPL"]["ok"])
        self.assertFalse(res["BAD"]["ok"])
        self.assertIn("boom", res["BAD"]["error"])

    def test_capture_many_concurrent_matches_sequential(self):
        from src.ingest.capture import capture_many

        good = self._fixture_frame()

        class Fake:
            def load(self, symbol):
                return good

        syms = ["AAA", "BBB", "CCC", "DDD"]
        with tempfile.TemporaryDirectory() as root:
            res = capture_many(syms, root, adapter=Fake(), today=dt.date(2026, 7, 2),
                               max_workers=4)
        self.assertEqual(set(res), set(syms))
        self.assertTrue(all(res[s]["ok"] for s in syms))

    def test_stale_session_is_skipped_not_written(self):
        # A frame whose session != the run day (dormant/stale chain) must NOT be written
        # to the wrong day's partition; it is reported skipped.
        from src.ingest.capture import capture_snapshot, capture_many

        good = self._fixture_frame()  # session 2026-07-02

        class Fake:
            def load(self, symbol):
                return good

        with tempfile.TemporaryDirectory() as root:
            res = capture_snapshot(Fake(), "AAPL", root, expected_session=dt.date(2026, 7, 1))
            self.assertFalse(res["ok"])
            self.assertTrue(res.get("skipped"))
            self.assertFalse(os.path.isdir(os.path.join(root, "symbol=AAPL")))
            # And through capture_many with a mismatched run day:
            res2 = capture_many(["AAPL"], root, adapter=Fake(), today=dt.date(2026, 7, 1))
            self.assertFalse(res2["AAPL"]["ok"])


@unittest.skipUnless(_HAVE_STACK, "pandas not installed")
class TestTradingDayGuard(unittest.TestCase):
    def test_weekend_and_holiday_skip_without_capturing(self):
        # R1: a non-trading day (Sat 2026-06-06; and the Fri 2026-07-03 July-4
        # observed holiday) must no-op and NOT write a partition.
        from src.ingest.capture import capture_many, is_trading_day

        class BoomAdapter:  # would raise if a capture were attempted
            def load(self, symbol):
                raise AssertionError("must not fetch on a non-trading day")

        for day in (dt.date(2026, 6, 6), dt.date(2026, 7, 3)):
            self.assertFalse(is_trading_day(day))
            res = capture_many(["AAPL"], "/tmp/should-not-write",
                               adapter=BoomAdapter(), today=day)
            self.assertIn("__skipped__", res)

    def test_trading_day_runs(self):
        from src.ingest.capture import capture_many, is_trading_day
        day = dt.date(2026, 6, 3)  # a Wednesday
        self.assertTrue(is_trading_day(day))

        class Fake:
            def load(self, symbol):
                raise ValueError("boom")  # runs (and is isolated), proving no skip

        res = capture_many(["X"], "/tmp/x", adapter=Fake(), today=day)
        self.assertNotIn("__skipped__", res)
        self.assertFalse(res["X"]["ok"])


if __name__ == "__main__":
    unittest.main()
