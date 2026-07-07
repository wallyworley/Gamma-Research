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
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from . import io as _io
from .adapter import ChainAdapter
from .market_calendar import is_trading_day

_log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")

# An EOD snapshot must be taken after the 16:00 ET close (a 15-min buffer for the feed
# to settle); before that the chain is an in-progress session and would be mislabeled as
# the close. The nightly runner gates on this so a reboot-triggered catch-up that fires
# in the morning no-ops instead of writing intraday data as EOD.
_MIN_EOD_RUN_ET = time(16, 15)


def is_after_close(now_et: datetime | None = None) -> bool:
    """True if the current ET wall-clock is past the post-close buffer (safe for EOD)."""
    return (now_et or datetime.now(_ET)).time() >= _MIN_EOD_RUN_ET


# ``is_trading_day`` (and the underlying NYSE holiday set) now live in
# ``src.ingest.market_calendar`` so this capture guard and the adapters' oi_asof
# dating read the same calendar (review finding F1). On a non-trading day the Cboe/
# Massive generation clock's ET date is a non-session day, so a capture would relabel
# the stale prior-session chain to a bogus session and write a junk partition (R1) -
# the guard below no-ops instead. Re-exported here to keep this module's public API
# unchanged.


def capture_snapshot(adapter: ChainAdapter, symbol: str, root: str, *,
                     expected_session: date | None = None) -> dict[str, Any]:
    """Load one symbol's chain via ``adapter`` and persist it. Returns a summary.

    The partition date is the snapshot's own session (from ``quote_ts``), not the wall
    clock, so an evening capture files under the correct trading day. When
    ``expected_session`` is given and the loaded frame's session differs (a dormant
    chain where nothing traded today, or a snapshot that has not rolled), the frame is
    NOT written - it would land in the wrong day's partition - and a skip is returned.
    """
    df = adapter.load(symbol)
    session_date = df["quote_ts"].iloc[0].date()
    if expected_session is not None and session_date != expected_session:
        _log.warning("%s: snapshot session %s != run day %s; skipping (stale/dormant chain)",
                     symbol.upper(), session_date.isoformat(), expected_session.isoformat())
        return {"ok": False, "symbol": symbol.upper(), "skipped": True,
                "error": f"session {session_date.isoformat()} != run day "
                         f"{expected_session.isoformat()}"}
    path = _io.write_canonical(df, root, symbol, session_date)
    return {"ok": True, "symbol": symbol.upper(), "contracts": int(len(df)),
            "session": session_date.isoformat(), "path": path}


def _capture_one(adapter: ChainAdapter, sym: str, root: str,
                 expected: date | None) -> tuple[Any, dict]:
    """Capture one symbol, converting any failure into a result dict (never raises)."""
    try:
        res = capture_snapshot(adapter, sym, root, expected_session=expected)
        if res["ok"]:
            _log.info("captured %s: %d contracts (session %s)",
                      res["symbol"], res["contracts"], res["session"])
        return sym, res
    except Exception as e:  # noqa: BLE001 - one bad symbol must not kill the batch
        _log.warning("capture failed for %s: %s: %s", sym, type(e).__name__, e)
        return sym, {"ok": False, "symbol": str(sym).upper(),
                     "error": f"{type(e).__name__}: {e}"}


def capture_many(symbols, root: str, *, adapter: ChainAdapter,
                 session_guard: bool = True, today: date | None = None,
                 max_workers: int = 1) -> dict[str, dict]:
    """Capture several symbols with one adapter. One symbol's failure does not stop
    the others - the returned dict records ok/error per symbol.

    With ``session_guard`` (default), a run on a non-trading day (weekend or listed
    market holiday) captures nothing and returns ``{"__skipped__": <reason>}`` so a
    daily cron no-ops instead of writing junk partitions (R1); on a trading day it also
    enforces that each captured frame's session equals the run day (drops stale/dormant
    chains rather than mislabel them). ``today`` overrides the ET run date (for tests).
    ``max_workers`` > 1 fetches concurrently (the adapters hold no per-call state);
    the full ~5,300-name universe needs this to finish inside the evening window.
    """
    expected: date | None = None
    if session_guard:
        day = today or datetime.now(_ET).date()
        if not is_trading_day(day):
            _log.info("non-trading day %s; skipping capture", day.isoformat())
            return {"__skipped__": f"non-trading day {day.isoformat()}"}
        expected = day

    symbols = list(symbols)
    results: dict[str, dict] = {}
    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for sym, res in pool.map(lambda s: _capture_one(adapter, s, root, expected), symbols):
                results[sym] = res
    else:
        for sym in symbols:
            _, res = _capture_one(adapter, sym, root, expected)
            results[sym] = res
    return results


__all__ = ["capture_snapshot", "capture_many", "is_trading_day", "is_after_close"]
