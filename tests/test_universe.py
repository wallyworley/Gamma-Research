"""Tests for the optionable-universe loader (src/ingest/universe.py).

Pure stdlib (no data stack, no network): parsing runs against a recorded Cboe CSV
sample, and the cache-fallback path uses a temp file.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ingest import universe  # noqa: E402

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "cboe_symboldir_sample.csv")


def _fixture_text():
    with open(_FIXTURE, encoding="utf-8") as fh:
        return fh.read()


class TestParseSymbolDirectory(unittest.TestCase):
    def test_splits_equities_and_indices(self):
        equities, indices = universe.parse_symbol_directory(_fixture_text())
        # AAPL (deduped) + BRK.B (dotted, kept); SPX/VIX routed to indices;
        # empty symbol and "BAD SYMBOL!" dropped.
        self.assertEqual(equities, ["AAPL", "BRK.B"])
        self.assertEqual(indices, ["SPX", "VIX"])

    def test_handles_leading_space_header(self):
        # Real Cboe header is " Stock Symbol" (leading space); must still be found.
        self.assertIn("Stock Symbol", " Stock Symbol".strip())
        equities, _ = universe.parse_symbol_directory(_fixture_text())
        self.assertIn("AAPL", equities)

    def test_empty_text_is_empty(self):
        self.assertEqual(universe.parse_symbol_directory(""), ([], []))


class TestPolygonMapping(unittest.TestCase):
    def test_index_detection_and_prefix(self):
        self.assertTrue(universe.is_index("SPX"))
        self.assertFalse(universe.is_index("AAPL"))
        self.assertEqual(universe.to_polygon_ticker("SPX"), "I:SPX")
        self.assertEqual(universe.to_polygon_ticker("aapl"), "AAPL")
        self.assertEqual(universe.to_polygon_ticker("BRK.B"), "BRK.B")


class TestLoadUniverse(unittest.TestCase):
    def test_falls_back_to_cache_when_offline(self):
        # fetch=False + an existing cache -> parse the cache, return equities only.
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cboe_symbols.csv")
            with open(cache, "w", encoding="utf-8") as fh:
                fh.write(_fixture_text())
            syms = universe.load_universe(fetch=False, cache_path=cache)
        self.assertEqual(syms, ["AAPL", "BRK.B"])

    def test_raises_when_no_source(self):
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "nope.csv")
            with self.assertRaises(RuntimeError):
                universe.load_universe(fetch=False, cache_path=missing)


if __name__ == "__main__":
    unittest.main()
