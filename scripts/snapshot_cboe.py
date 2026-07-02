#!/usr/bin/env python3
"""Daily Cboe options snapshot -> canonical parquet store (build history forward).

Cboe is snapshot-only, so run this once per session (after the US close) to
accumulate the history a backtest needs. Equities only (index AM/PM settlement is
not yet supported by the adapter).

Usage:
    python3 scripts/snapshot_cboe.py [SYMBOL ...]      # default: AAPL

Data root:  $GAMMA_DATA_DIR, else <repo>/data/normalized  (git-ignored).
Idempotent: re-running a session overwrites that session's partition.
Exit code:  0 if every symbol captured, 1 otherwise.
"""
import logging
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingest.adapters.cboe import CboeAdapter  # noqa: E402
from src.ingest.capture import capture_many  # noqa: E402


def main(argv: list[str]) -> int:
    symbols = list(dict.fromkeys(s.upper() for s in argv)) or ["AAPL"]  # dedupe (N1)
    root = os.environ.get("GAMMA_DATA_DIR") or str(REPO / "data" / "normalized")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    results = capture_many(symbols, root, adapter=CboeAdapter())
    if "__skipped__" in results:  # non-trading day: clean no-op, not a failure (R1)
        print(f"skipped: {results['__skipped__']}")
        return 0
    ok = [s for s, r in results.items() if r["ok"]]
    for sym, r in results.items():
        if not r["ok"]:
            print(f"  FAILED {sym}: {r['error']}", file=sys.stderr)
    print(f"captured {len(ok)}/{len(symbols)} symbols into {root}")
    return 0 if len(ok) == len(symbols) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
