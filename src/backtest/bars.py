"""Daily OHLC bars from Massive/Polygon aggregates, for the backtester's return side.

A GEX backtest has two halves: the *signal* (from option chains, captured nightly) and
the *returns* (the underlying's daily bars). Unlike option greeks/OI, daily equity bars
ARE available historically on the Options Starter tier (~2 years), so the return side can
be backfilled today. `fetch_daily_bars` returns a frame in exactly the shape
`backtest.run_backtest` / `validate_bars` expect: a sorted, unique DatetimeIndex with
positive open/close (plus high/low/volume for convenience).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date
from typing import Any

import pandas as pd

_BASE = "https://api.polygon.io"
_TIMEOUT = 30
_RETRIES = 4
_RETRY_CODES = frozenset({429, 500, 502, 503, 504})


def _get(url: str, api_key: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}", "User-Agent": "gamma-research/1.0"})
    last: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_CODES or attempt == _RETRIES - 1:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == _RETRIES - 1:
                raise
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last  # unreachable


def bars_from_aggregates(results: list[dict]) -> pd.DataFrame:
    """Shape Polygon daily-aggregate results into a canonical bars frame (pure, testable)."""
    if not results:
        raise ValueError("no aggregate bars")
    idx = pd.to_datetime([b["t"] for b in results], unit="ms").normalize()
    df = pd.DataFrame({
        "open": [float(b["o"]) for b in results],
        "high": [float(b.get("h")) if b.get("h") is not None else None for b in results],
        "low": [float(b.get("l")) if b.get("l") is not None else None for b in results],
        "close": [float(b["c"]) for b in results],
        "volume": [float(b.get("v")) if b.get("v") is not None else None for b in results],
    }, index=idx)
    df.index.name = "ts"
    return df[~df.index.duplicated(keep="last")].sort_index()


def fetch_daily_bars(ticker: str, start: "date | str", end: "date | str", *,
                     api_key: str | None = None, adjusted: bool = True) -> pd.DataFrame:
    """Daily bars for ``ticker`` over [start, end] (inclusive), sorted/unique DatetimeIndex.

    Split/dividend-adjusted by default. ``ticker`` may be an index in Polygon form (e.g.
    ``I:SPX``). Raises if the window has no bars.
    """
    api_key = api_key or os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        raise ValueError("MASSIVE_API_KEY not set (pass api_key= or set MASSIVE_API_KEY)")
    s = start.isoformat() if hasattr(start, "isoformat") else str(start)
    e = end.isoformat() if hasattr(end, "isoformat") else str(end)
    url = (f"{_BASE}/v2/aggs/ticker/{ticker.upper()}/range/1/day/{s}/{e}"
           f"?adjusted={'true' if adjusted else 'false'}&sort=asc&limit=50000")
    results = _get(url, api_key).get("results") or []
    if not results:
        raise ValueError(f"no daily bars for {ticker} over {s}..{e}")
    return bars_from_aggregates(results)


__all__ = ["fetch_daily_bars", "bars_from_aggregates"]
