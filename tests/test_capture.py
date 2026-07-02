"""Tests for the snapshot capture (src/ingest/capture.py). Needs the data stack.

Uses a fake adapter (no live HTTP): the frame comes from normalizing the recorded
Cboe fixture, so the write/read partition path is exercised deterministically.
"""

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
            # partition is the snapshot's session, and reads back valid.
            back = io.read_canonical(root, "AAPL", res["session"])
            self.assertEqual(len(back), 6)
            self.assertEqual(schema.validate_frame(back), [])

    def test_capture_many_isolates_failures(self):
        from src.ingest.capture import capture_many

        good = self._fixture_frame()

        class Mixed:
            def load(self, symbol):
                if symbol == "BAD":
                    raise ValueError("boom")
                return good

        with tempfile.TemporaryDirectory() as root:
            res = capture_many(["AAPL", "BAD"], root, adapter=Mixed())
        self.assertTrue(res["AAPL"]["ok"])
        self.assertFalse(res["BAD"]["ok"])
        self.assertIn("boom", res["BAD"]["error"])


if __name__ == "__main__":
    unittest.main()
