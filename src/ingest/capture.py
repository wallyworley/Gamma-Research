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
from typing import Any

from . import io as _io
from .adapter import ChainAdapter

_log = logging.getLogger(__name__)


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


def capture_many(symbols, root: str, *, adapter: ChainAdapter) -> dict[str, dict]:
    """Capture several symbols with one adapter. One symbol's failure does not stop
    the others - the returned dict records ok/error per symbol.
    """
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


__all__ = ["capture_snapshot", "capture_many"]
