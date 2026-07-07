#!/usr/bin/env python3
"""Resumable ThetaData EOD backfill -> canonical parquet store.

Walks the trading days of a date range (shared NYSE calendar) for each requested
symbol and writes one canonical partition per symbol per session via the ThetaData
adapter. This is how the store gets HISTORY the nightly snapshot source cannot backfill
(Massive/Polygon only ever returns the current session; OI/greeks are not reconstructable
after the fact). ThetaData was field-validated against our own captures: open interest
matched 100.00% contract-for-contract and their underlying_price matched our spot within
0.14%, so backfilled sessions are directly comparable to nightly ones.

Resumable BY CONSTRUCTION: a (symbol, session) whose partition already exists is skipped,
so an interrupted run just re-run picks up where it stopped, and re-pointing at a
populated store is a cheap no-op. Per-(symbol, session) failures are isolated and counted;
one bad day never aborts the batch. A session before the subscription's history floor (or
before a symbol listed) returns no data and is counted as a clean skip, not a failure.

Usage:
    python3 scripts/backfill_thetadata.py SYMBOL... --start YYYY-MM-DD --end YYYY-MM-DD \
        [--workers 2] [--root DIR]

    SYMBOL ...        one or more underlyings (equities and/or index roots: SPX NDX ...)
    --start/--end     inclusive session range (only NYSE trading days are attempted)
    --workers K       concurrent (symbol, session) fetches (default 2 = Standard-tier
                      server threads; raising it past your entitlement only causes 429s)
    --root DIR        parquet root (else $GAMMA_DATA_DIR, else <repo>/data/normalized)

Env:
    THETADATA_API_KEY   required (the client also reads it from .env in the cwd)
    GAMMA_DATA_DIR      parquet root when --root is not given

Concurrency model: work items are (symbol, session) pairs across the whole request, run
through one ThreadPoolExecutor capped at --workers. One shared adapter (one authenticated
client) is used by every thread; the client's grpc channel is safe for concurrent calls
and the adapter holds no per-call state.

Exit code: 0 normally (skips and isolated failures are expected); 1 only if every attempted
session failed (a total failure worth alerting on); 2 if the API key is missing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingest import io as _io  # noqa: E402
from src.ingest.adapter import ChainAdapter  # noqa: E402
from src.ingest.adapters.thetadata import NoDataForSession, ThetadataAdapter  # noqa: E402
from src.ingest.market_calendar import is_trading_day  # noqa: E402
from src.ingest.universe import INDEX_CAPTURE_ROOTS  # noqa: E402

_log = logging.getLogger("backfill_thetadata")
_STATUS_FILE = ".backfill_status.json"
_STATUS_EVERY = 25   # flush the heartbeat at least this often during a long run


def _as_date(value: "date | str") -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _trading_days(start: date, end: date) -> list[date]:
    """Every NYSE trading day in [start, end] (weekends and holidays excluded up front,
    so no request is wasted on a closed session)."""
    days, d = [], start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def _partition_exists(root: str, symbol: str, session: date) -> bool:
    """True if a chain partition is already stored for (symbol, session) - the resume gate."""
    return any(_io.iter_partitions(root, symbol=symbol, start=session, end=session))


def _write_status(root: str, payload: dict) -> None:
    """Best-effort heartbeat so a stalled/crashed backfill is detectable from the store."""
    try:
        Path(root).mkdir(parents=True, exist_ok=True)
        Path(root, _STATUS_FILE).write_text(json.dumps(payload, indent=1), encoding="utf-8")
    except OSError as e:
        _log.warning("could not write %s: %s", _STATUS_FILE, e)


def run_backfill(symbols, start: "date | str", end: "date | str", root: str, *,
                 adapter: ChainAdapter, workers: int = 2) -> dict:
    """Fetch + normalize + write every missing (symbol, session) in the range.

    Returns count dict: written / skipped_existing / no_data / failed. Existing partitions
    are skipped (resumable); ``NoDataForSession`` is a clean skip; any other exception is an
    isolated failure (the batch continues). ``workers`` fetches run concurrently.
    """
    start, end = _as_date(start), _as_date(end)
    symbols = list(symbols)                     # materialize (may be iterated twice)
    sessions = _trading_days(start, end)

    pending: list[tuple[str, date]] = []
    skipped_existing = 0
    for sym in symbols:
        s = sym.upper()
        for sess in sessions:
            if _partition_exists(root, s, sess):
                skipped_existing += 1
            else:
                pending.append((s, sess))

    counts = {"written": 0, "skipped_existing": skipped_existing, "no_data": 0, "failed": 0}
    lock = threading.Lock()
    started = datetime.now(timezone.utc).isoformat()

    def _status(done: bool = False) -> None:
        _write_status(root, {
            "ts": datetime.now(timezone.utc).isoformat(), "started": started,
            "start": start.isoformat(), "end": end.isoformat(),
            "symbols": len(symbols), "sessions": len(sessions),
            "pending": len(pending), "done": done, **counts})

    def _work(item: tuple[str, date]) -> tuple[str, str, date, str | None]:
        sym, sess = item
        try:
            df = adapter.load(sym, sess)
            _io.write_canonical(df, root, sym, sess)
            return ("written", sym, sess, f"{len(df)} contracts")
        except NoDataForSession as e:               # before history floor / not listed
            return ("no_data", sym, sess, str(e))
        except Exception as e:                       # noqa: BLE001 - isolate, keep going
            return ("failed", sym, sess, f"{type(e).__name__}: {e}")

    def _record(result, n_done: int) -> None:
        kind, sym, sess, detail = result
        with lock:
            counts[kind] += 1
        if kind == "written":
            _log.info("wrote %s %s (%s)", sym, sess.isoformat(), detail)
        elif kind == "no_data":
            _log.info("no data %s %s (clean skip)", sym, sess.isoformat())
        else:
            _log.warning("FAILED %s %s: %s", sym, sess.isoformat(), detail)
        if n_done % _STATUS_EVERY == 0:
            _status()

    _status()
    if workers > 1 and pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for n, result in enumerate(pool.map(_work, pending), start=1):
                _record(result, n)
    else:
        for n, item in enumerate(pending, start=1):
            _record(_work(item), n)
    _status(done=True)
    return counts


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Resumable ThetaData EOD backfill")
    ap.add_argument("symbols", nargs="+", help="underlyings (equities and/or index roots)")
    ap.add_argument("--start", required=True, help="first session YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", required=True, help="last session YYYY-MM-DD (inclusive)")
    ap.add_argument("--workers", type=int, default=2,
                    help="concurrent fetches (default 2 = Standard-tier server threads)")
    ap.add_argument("--root", default=None, help="parquet root (else $GAMMA_DATA_DIR / repo data)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.environ.get("THETADATA_API_KEY"):
        print("THETADATA_API_KEY not set", file=sys.stderr)
        return 2

    root = args.root or os.environ.get("GAMMA_DATA_DIR") or str(REPO / "data" / "normalized")
    symbols = list(dict.fromkeys(s.upper() for s in args.symbols))

    # Index roots multi-query (SPX -> SPX+SPXW, ...) automatically; equities are unaffected
    # because the adapter only expands roots for symbols listed in index_roots.
    adapter = ThetadataAdapter(index_roots=frozenset(INDEX_CAPTURE_ROOTS))
    _log.info("backfilling %d symbol(s) %s..%s -> %s (workers=%d)",
              len(symbols), args.start, args.end, root, args.workers)
    counts = run_backfill(symbols, args.start, args.end, root,
                          adapter=adapter, workers=args.workers)

    attempted = counts["written"] + counts["no_data"] + counts["failed"]
    print(f"backfill done: {counts['written']} written, "
          f"{counts['skipped_existing']} already-stored, {counts['no_data']} no-data, "
          f"{counts['failed']} failed -> {root}")
    if attempted and counts["written"] == 0 and counts["no_data"] == 0:
        return 1   # every attempted session failed: a total failure worth alerting on
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
