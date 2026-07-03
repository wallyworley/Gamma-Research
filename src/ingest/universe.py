"""Optionable-underlying universe from the Cboe symbol directory.

The full set of US names with listed options (~5,300) is published by Cboe as a CSV.
We download + cache it; the cache is the fallback when the download fails, so a nightly
job still runs if Cboe is briefly unreachable. Cash-settled index symbols are separated
out: on Massive/Polygon they need an ``I:`` ticker prefix and a settlement/entitlement
story the equity path does not, so ``load_universe`` returns equities only for now and
indices are surfaced separately for a later pass.

    from src.ingest.universe import load_universe
    symbols = load_universe()            # ~5,293 optionable equity underlyings
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import urllib.request
from pathlib import Path

_log = logging.getLogger(__name__)

_URL = "https://www.cboe.com/us/options/symboldir/equity_index_options/?download=csv"
_SYMBOL_COL = "Stock Symbol"
_HTTP_TIMEOUT = 60
# A healthy Cboe directory carries ~5,300 names. A body that parses to far fewer is a
# maintenance page / truncated response, not the directory: never cache it, never trust
# it (guards against poisoning the only fallback copy of a source with no history).
_MIN_EQUITIES = 1000

# Cash-settled indices in the Cboe directory: they need Polygon's ``I:`` prefix and a
# distinct settlement handling, so the equity nightly excludes them (surfaced via
# parse_symbol_directory for a later index pass).
INDEX_SYMBOLS = frozenset({
    "SPX", "SPXW", "VIX", "VIXW", "NDX", "NDXP", "RUT", "RUTW", "MRUT",
    "XSP", "DJX", "OEX", "XEO", "NANOS", "VXST",
})
# Canonical, path-safe ticker charset (matches schema.partition_relpath's guard).
_SYMBOL_RE = re.compile(r"[A-Z0-9.]{1,6}$")


def _default_cache() -> str:
    root = os.environ.get("GAMMA_DATA_DIR") or str(Path(__file__).resolve().parents[2] / "data")
    return str(Path(root) / "universe" / "cboe_symbols.csv")


def _download(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "gamma-research/1.0"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # follows redirects
        return resp.read().decode("utf-8", "replace")


def parse_symbol_directory(text: str) -> tuple[list[str], list[str]]:
    """Parse the Cboe CSV into (equities, indices), each sorted + de-duplicated.

    Rows whose symbol is empty or not a canonical, path-safe ticker are dropped.
    """
    equities: set[str] = set()
    indices: set[str] = set()
    reader = csv.DictReader(io.StringIO(text))
    col = _SYMBOL_COL
    if reader.fieldnames:  # tolerate leading spaces in the header (" Stock Symbol")
        stripped = {f.strip(): f for f in reader.fieldnames}
        col = stripped.get(_SYMBOL_COL, _SYMBOL_COL)
    for row in reader:
        sym = (row.get(col) or "").strip().upper()
        if not sym or not _SYMBOL_RE.match(sym):
            continue
        (indices if sym in INDEX_SYMBOLS else equities).add(sym)
    return sorted(equities), sorted(indices)


def load_universe(*, url: str = _URL, cache_path: str | None = None,
                  fetch: bool = True, min_equities: int = _MIN_EQUITIES) -> list[str]:
    """The optionable **equity** underlyings (indices excluded; see module docstring).

    Downloads the Cboe directory, parses it, and only then refreshes the cache - and
    only if the parse clears ``min_equities`` (a non-CSV 200 body is discarded, never
    cached). On download failure or a too-small body, falls back to the cache. Raises if
    no usable source exists (download unusable AND no/again-too-small cache).
    """
    cache_path = cache_path or _default_cache()

    if fetch:
        try:
            text = _download(url)
            equities, indices = parse_symbol_directory(text)
            if len(equities) >= min_equities:
                try:
                    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(cache_path).write_text(text, encoding="utf-8")
                except OSError as e:
                    _log.warning("could not refresh universe cache %s: %s", cache_path, e)
                _log.info("universe: %d optionable equities (+%d indices) [live]",
                          len(equities), len(indices))
                return equities
            _log.warning("universe download parsed only %d equities (< %d floor); "
                         "discarding it and falling back to cache", len(equities), min_equities)
        except Exception as e:  # noqa: BLE001 - fall back to cache on any download/parse error
            _log.warning("universe download failed (%s: %s); falling back to cache",
                         type(e).__name__, e)

    if os.path.exists(cache_path):
        equities, indices = parse_symbol_directory(
            Path(cache_path).read_text(encoding="utf-8"))
        if len(equities) >= min_equities:
            _log.info("universe: %d optionable equities (+%d indices) [cache %s]",
                      len(equities), len(indices), cache_path)
            return equities
        raise RuntimeError(f"universe cache {cache_path} parsed only {len(equities)} "
                           f"equities (< {min_equities}); refusing to run on a bad universe")
    raise RuntimeError(f"universe unavailable: no usable download and no cache at {cache_path}")


def is_index(symbol: str) -> bool:
    return symbol.upper() in INDEX_SYMBOLS


def to_polygon_ticker(symbol: str) -> str:
    """Map a canonical symbol to its Massive/Polygon snapshot ticker (``I:`` for indices)."""
    s = symbol.upper()
    return f"I:{s}" if s in INDEX_SYMBOLS else s


__all__ = ["load_universe", "parse_symbol_directory", "is_index",
           "to_polygon_ticker", "INDEX_SYMBOLS"]
