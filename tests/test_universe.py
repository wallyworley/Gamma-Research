"""Tests for the optionable-universe loader (src/ingest/universe.py).

Pure stdlib (no data stack, no network): parsing runs against a recorded Cboe CSV
sample, and the cache-fallback path uses a temp file.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

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
        # Real Cboe header is " Stock Symbol" (leading space). If the strip-map broke,
        # the column wouldn't be found and the parse would be empty.
        equities, _ = universe.parse_symbol_directory(_fixture_text())
        self.assertEqual(equities, ["AAPL", "BRK.B"])  # dotted BRK.B kept too

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
        # (min_equities=1 since the tiny fixture is below the real 1,000 floor.)
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cboe_symbols.csv")
            Path(cache).write_text(_fixture_text(), encoding="utf-8")
            syms = universe.load_universe(fetch=False, cache_path=cache, min_equities=1)
        self.assertEqual(syms, ["AAPL", "BRK.B"])

    def test_raises_when_no_source(self):
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "nope.csv")
            with self.assertRaises(RuntimeError):
                universe.load_universe(fetch=False, cache_path=missing, min_equities=1)

    def test_include_indices_appends_capture_roots(self):
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cboe_symbols.csv")
            Path(cache).write_text(_fixture_text(), encoding="utf-8")
            eq = universe.load_universe(fetch=False, cache_path=cache, min_equities=1)
            with_idx = universe.load_universe(fetch=False, cache_path=cache, min_equities=1,
                                              include_indices=True)
        self.assertEqual(with_idx, eq + list(universe.INDEX_CAPTURE_ROOTS))
        self.assertIn("SPX", with_idx)
        self.assertNotIn("SPX", eq)

    def test_poisoned_download_does_not_overwrite_good_cache(self):
        # A 200-OK non-CSV body (maintenance page) parses below the floor: it must be
        # discarded, the good cache left intact, and the cached universe returned.
        orig = universe._download
        universe._download = lambda url: "<html><body>maintenance</body></html>"
        try:
            with tempfile.TemporaryDirectory() as d:
                cache = os.path.join(d, "cboe_symbols.csv")
                Path(cache).write_text(_fixture_text(), encoding="utf-8")
                syms = universe.load_universe(fetch=True, cache_path=cache, min_equities=2)
                cached = Path(cache).read_text(encoding="utf-8")
        finally:
            universe._download = orig
        self.assertEqual(syms, ["AAPL", "BRK.B"])   # served from the good cache
        self.assertIn("AAPL", cached)               # cache NOT overwritten
        self.assertNotIn("maintenance", cached)

    def test_too_small_cache_raises_rather_than_run(self):
        # If the cache itself is poisoned/tiny and no download, refuse loudly.
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cboe_symbols.csv")
            Path(cache).write_text(_fixture_text(), encoding="utf-8")  # only 2 equities
            with self.assertRaises(RuntimeError):
                universe.load_universe(fetch=False, cache_path=cache, min_equities=1000)


if __name__ == "__main__":
    unittest.main()
