"""Capture point-in-time option-chain snapshots into the canonical parquet store.

Cboe (and other snapshot-only sources) have no history, so a backtest has to build
its own: run this once per session (after the US close) and it appends one
partition per symbol per session. Idempotent - re-running a session overwrites its
own partition (`symbol=<SYM>/date=<YYYY-MM-DD>/chain.parquet`).

Signal-/vendor-agnostic: takes any `ChainAdapter`, so the same capture works for
Cboe today and a paid vendor later. `scripts/snapshot_cboe.py` is the CLI wrapper.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import io as _io
from .adapter import ChainAdapter

_log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")

# NYSE full-day market holidays (weekday closures only; weekends handled separately).
# Hardcoded to avoid a calendar dependency - MAINTAIN yearly or swap for the
# `exchange_calendars` package. On a non-trading day the Cboe generation clock's ET
# date is a non-session day, so a capture would relabel the stale prior-session chain
# to a bogus session and write a junk partition (review finding R1).
_MARKET_HOLIDAYS = frozenset({
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
})


def is_trading_day(d: date) -> bool:
    """True if ``d`` is a NYSE session (weekday and not a listed market holiday)."""
    return d.weekday() < 5 and d not in _MARKET_HOLIDAYS


def capture_snapshot(adapter: ChainAdapter, symbol: str, root: str) -> dict[str, Any]:
    """Load one symbol's chain via ``adapter`` and persist it. Returns a summary.

    The partition date is the snapshot's own session (from ``quote_ts``), not the
    wall clock, so an evening capture files under the correct trading day.
    """
    df = adapter.load(symbol)
    session_date = df["quote_ts"].iloc[0].date()
    path = _io.write_canonical(df, root, symbol, session_date)
    return {"ok": True, "symbol": symbol.upper(), "contracts": int(len(df)),
            "session": session_date.isoformat(), "path": path}


def capture_many(symbols, root: str, *, adapter: ChainAdapter,
                 session_guard: bool = True, today: date | None = None) -> dict[str, dict]:
    """Capture several symbols with one adapter. One symbol's failure does not stop
    the others - the returned dict records ok/error per symbol.

    With ``session_guard`` (default), a run on a non-trading day (weekend or listed
    market holiday) captures nothing and returns ``{"__skipped__": <reason>}`` so a
    daily cron no-ops instead of writing junk partitions (R1). ``today`` overrides
    the ET run date (for tests).
    """
    if session_guard:
        day = today or datetime.now(_ET).date()
        if not is_trading_day(day):
            _log.info("non-trading day %s; skipping capture", day.isoformat())
            return {"__skipped__": f"non-trading day {day.isoformat()}"}

    results: dict[str, dict] = {}
    for sym in symbols:
        try:
            res = capture_snapshot(adapter, sym, root)
            _log.info("captured %s: %d contracts (session %s) -> %s",
                      res["symbol"], res["contracts"], res["session"], res["path"])
            results[sym] = res
        except Exception as e:  # noqa: BLE001 - one bad symbol must not kill the batch
            _log.warning("capture failed for %s: %s: %s", sym, type(e).__name__, e)
            results[sym] = {"ok": False, "symbol": str(sym).upper(),
                            "error": f"{type(e).__name__}: {e}"}
    return results


__all__ = ["capture_snapshot", "capture_many", "is_trading_day"]
