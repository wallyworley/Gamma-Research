#!/usr/bin/env python3
"""Nightly full-universe option-chain snapshot via Massive/Polygon -> canonical store.

Fetches every optionable equity underlying (Cboe symbol directory, ~5,293 names) and
writes one canonical parquet partition per symbol per session. Meant to run once per
weekday after the US close (see deploy/systemd/gamma-snapshot.timer, 17:30 ET); the
capture layer's trading-day + session guards make it a clean no-op on holidays and drop
any stale/dormant chain rather than mislabel it.

Usage:
    python3 scripts/snapshot_universe.py [--limit N] [--workers K] [SYMBOL ...]

    (no SYMBOLs)      capture the full Cboe equity universe
    SYMBOL ...        capture just those (smoke test), still via MassiveAdapter
    --limit N         cap the universe to the first N symbols (smoke test)
    --workers K       concurrent fetches (default 8)

Env:
    MASSIVE_API_KEY   required (Bearer auth)
    GAMMA_DATA_DIR    parquet root (else <repo>/data/normalized)
Exit code: 0 unless zero symbols were captured (total failure).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingest.adapters.massive import MassiveAdapter  # noqa: E402
from src.ingest.capture import capture_many, is_after_close  # noqa: E402
from src.ingest.universe import load_universe  # noqa: E402

_STATUS_FILE = ".last_run.json"


def _write_status(root: str, payload: dict) -> None:
    """Best-effort heartbeat so a silently-failing timer is detectable (staleness = alarm)."""
    try:
        Path(root).mkdir(parents=True, exist_ok=True)
        Path(root, _STATUS_FILE).write_text(json.dumps(payload, indent=1), encoding="utf-8")
    except OSError as e:
        logging.warning("could not write %s: %s", _STATUS_FILE, e)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Nightly Massive/Polygon universe snapshot")
    ap.add_argument("symbols", nargs="*", help="explicit symbols (default: full universe)")
    ap.add_argument("--limit", type=int, default=None, help="cap universe to first N (smoke test)")
    ap.add_argument("--workers", type=int, default=8, help="concurrent fetches (default 8)")
    ap.add_argument("--force", action="store_true",
                    help="run even before the post-close window (skips the after-close gate)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.environ.get("MASSIVE_API_KEY"):
        print("MASSIVE_API_KEY not set", file=sys.stderr)
        return 2

    root = os.environ.get("GAMMA_DATA_DIR") or str(REPO / "data" / "normalized")

    # After-close gate: a snapshot taken intraday would be mislabeled as the close, and a
    # reboot-triggered Persistent catch-up can fire in the morning. Refuse unless forced.
    if not args.force and not is_after_close():
        msg = "before the 16:15 ET post-close window; skipping (use --force to override)"
        logging.info(msg)
        _write_status(root, {"ts": datetime.now(timezone.utc).isoformat(), "skipped": msg})
        return 0

    if args.symbols:
        symbols = list(dict.fromkeys(s.upper() for s in args.symbols))
    else:
        symbols = load_universe()
    if args.limit is not None:
        symbols = symbols[:args.limit]

    logging.info("capturing %d symbols -> %s (workers=%d)", len(symbols), root, args.workers)
    results = capture_many(symbols, root, adapter=MassiveAdapter(), max_workers=args.workers)

    if "__skipped__" in results:  # non-trading day: clean no-op
        print(f"skipped: {results['__skipped__']}")
        _write_status(root, {"ts": datetime.now(timezone.utc).isoformat(),
                             "skipped": results["__skipped__"]})
        return 0

    ok = [s for s, r in results.items() if r.get("ok")]
    skipped = [s for s, r in results.items() if r.get("skipped")]
    failed = [s for s, r in results.items() if not r.get("ok") and not r.get("skipped")]
    for sym in failed[:50]:
        print(f"  FAILED {sym}: {results[sym]['error']}", file=sys.stderr)
    if len(failed) > 50:
        print(f"  ... and {len(failed) - 50} more failures", file=sys.stderr)

    _write_status(root, {"ts": datetime.now(timezone.utc).isoformat(),
                         "requested": len(symbols), "ok": len(ok),
                         "stale_skipped": len(skipped), "failed": len(failed)})
    print(f"captured {len(ok)}/{len(symbols)} "
          f"({len(skipped)} stale-skipped, {len(failed)} failed) into {root}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
