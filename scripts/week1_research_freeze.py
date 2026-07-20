"""Create deterministic Week-1 coverage, outlier, and eligibility artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.vol_forecast_experiment import load_bars, load_series  # noqa: E402
from src.research.audit import audit_series_and_bars, file_sha256    # noqa: E402
from src.research.registry import load_and_verify_manifest           # noqa: E402


def run(manifest_path: str, inputs: list[tuple[str, str, str]]) -> dict:
    manifest = load_and_verify_manifest(manifest_path)
    prospective_start = manifest["validation"]["prospective_holdout_start"]
    results = []
    for symbol, series_path, bars_path in inputs:
        audit = audit_series_and_bars(
            load_series(series_path), load_bars(bars_path), symbol=symbol,
            prospective_start=prospective_start,
            minimum_history=manifest["universe"]["minimum_history_sessions"],
        )
        audit["inputs"] = {
            "series": str(series_path), "series_sha256": file_sha256(series_path),
            "bars": str(bars_path), "bars_sha256": file_sha256(bars_path),
        }
        results.append(audit)
    return {
        "experiment_id": manifest["experiment_id"],
        "manifest": str(manifest_path),
        "manifest_hash": manifest["manifest_hash"],
        "generated_on": date.today().isoformat(),
        "outcome_fields_examined": [],
        "symbols": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--input", nargs=3, action="append", metavar=("SYMBOL", "SERIES", "BARS"),
                    required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    payload = run(args.manifest, [tuple(x) for x in args.input])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"wrote": args.out, "symbols": [x["symbol"] for x in payload["symbols"]],
                      "manifest_hash": payload["manifest_hash"]}, indent=2))


if __name__ == "__main__":
    main()
