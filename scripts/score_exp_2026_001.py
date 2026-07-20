#!/usr/bin/env python3
"""Development-only scorer for locked EXP-2026-001.

This command refuses any bar file containing a prospective-holdout session.  It
may consume chain features from later dates because those contain no outcomes,
but it slices them to the development window before joining prices or targets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.vol_forecast_experiment import load_bars  # noqa: E402
from src.research.audit import file_sha256  # noqa: E402
from src.research.features import add_price_features  # noqa: E402
from src.research.holdout import (  # noqa: E402
    assert_frozen_development_bars,
    development_access_record,
    load_holdout_policy,
)
from src.research.registry import load_and_verify_manifest  # noqa: E402
from src.research.walkforward import (  # noqa: E402
    DEFAULT_CONTROLS,
    coverage_by_year,
    next_day_absolute_log_return,
    walk_forward_score,
)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_chain_features(path: str | Path) -> pd.DataFrame:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    frame = pd.DataFrame(payload)
    if frame.empty or "date" not in frame:
        raise ValueError(f"invalid/empty chain feature file: {path}")
    frame.index = pd.to_datetime(frame.pop("date"))
    if frame.index.has_duplicates:
        raise ValueError(f"duplicate chain-feature sessions: {path}")
    return frame.sort_index()


def score_symbol(*, symbol: str, chain_path: str, bars_path: str,
                 market_bars_path: str, manifest: dict, policy,
                 bootstrap_samples: int, placebo_permutations: int) -> dict:
    bars = load_bars(bars_path)
    market_bars = load_bars(market_bars_path)
    assert_frozen_development_bars(bars, policy, source=bars_path)
    assert_frozen_development_bars(market_bars, policy, source=market_bars_path)

    development_end = pd.Timestamp(manifest["validation"]["development_period"][1])
    chain = load_chain_features(chain_path)
    chain = chain[(chain.index <= development_end) & (chain.index < policy.start)]
    panel = add_price_features(chain, bars, market_bars["close"])
    panel["target_abs_log_return"] = next_day_absolute_log_return(bars).reindex(panel.index)

    primary = symbol.upper() == "SPY"
    signal = "gex_norm_bs_empirical" if primary else "gex_norm_bs_naive"
    score = walk_forward_score(
        panel, signal=signal, controls=DEFAULT_CONTROLS,
        bootstrap_samples=bootstrap_samples,
        placebo_permutations=placebo_permutations,
    )
    gates = manifest["pass_fail"]
    gate_results = {
        "minimum_oos_squared_error_improvement": (
            score["oos_squared_error_improvement"]
            > float(gates["minimum_oos_squared_error_improvement"])
        ),
        "block_bootstrap_one_sided_p": (
            score["moving_block_bootstrap"]["one_sided_p_mean_loss_gain_le_zero"]
            <= float(gates["block_bootstrap_one_sided_p_max"])
        ),
        "annual_fold_sign_consistency": (
            score["annual_fold_sign_consistency"]
            >= float(gates["annual_fold_sign_consistency_min"])
        ),
        "placebo_percentile": (
            score["block_permutation_placebo"]["observed_percentile"]
            >= float(gates["placebo_percentile_min"])
        ),
    }
    score["gate_results"] = gate_results
    score["passes_all_registered_gates"] = bool(all(gate_results.values()))

    coverage_columns = DEFAULT_CONTROLS + ("day_of_week", signal, "target_abs_log_return")
    return {
        "symbol": symbol.upper(),
        "inference_role": "primary" if primary else "naive-sign replication sensitivity",
        "signal": signal,
        "development_panel_span": [str(panel.index.min().date()), str(panel.index.max().date())],
        "development_panel_rows": int(len(panel)),
        "coverage_by_year": coverage_by_year(panel, coverage_columns),
        "score": score,
        "input_hashes": {
            "chain_features": file_sha256(chain_path),
            "development_bars": file_sha256(bars_path),
            "development_market_bars": file_sha256(market_bars_path),
        },
        "outcome_access": development_access_record(
            policy, bars_source=bars_path, first_session=bars.index.min(),
            last_session=bars.index.max(), rows=len(bars),
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--holdout-policy", required=True)
    ap.add_argument("--input", nargs=4, action="append", required=True,
                    metavar=("SYMBOL", "CHAIN_FEATURES", "DEV_BARS", "DEV_MARKET_BARS"))
    ap.add_argument("--bootstrap-samples", type=int, default=2000)
    ap.add_argument("--placebo-permutations", type=int, default=100)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    manifest = load_and_verify_manifest(args.manifest)
    policy = load_holdout_policy(args.holdout_policy)
    if policy.parent_experiment != manifest["experiment_id"]:
        raise ValueError("holdout policy does not belong to the supplied experiment")

    results = [
        score_symbol(
            symbol=symbol, chain_path=chain, bars_path=bars,
            market_bars_path=market, manifest=manifest, policy=policy,
            bootstrap_samples=args.bootstrap_samples,
            placebo_permutations=args.placebo_permutations,
        )
        for symbol, chain, bars, market in args.input
    ]
    primary = next(x for x in results if x["inference_role"] == "primary")
    payload = {
        "experiment_id": manifest["experiment_id"],
        "manifest_hash": manifest["manifest_hash"],
        "holdout_policy_id": policy.experiment_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "development_only",
        "holdout_scored": False,
        "results": results,
        "day_30_decision": (
            "GO_TO_PROSPECTIVE_WAIT" if primary["score"]["passes_all_registered_gates"]
            else "STOP_EOD_OI_GEX_LEVEL_AS_STANDALONE_ALPHA"
        ),
        "legacy_vendor_gamma_results": {
            "status": "quarantined_invalid_for_inference",
            "reason": "saved July 8 scorecards used vendor gamma later shown to contain solver-print contamination",
        },
    }
    atomic_json(Path(args.out), payload)
    print(json.dumps({
        "out": args.out,
        "decision": payload["day_30_decision"],
        "primary_passes": primary["score"]["passes_all_registered_gates"],
    }, indent=2))


if __name__ == "__main__":
    main()
