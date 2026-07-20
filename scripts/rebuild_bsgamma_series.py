"""Rebuild GEX history using BS gamma derived only from valid stored IV.

Outputs are atomic and use new filenames. Raw chains and vendor-gamma series are
never modified. Progress is checkpointed for unattended VPS execution.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import EngineConfig                              # noqa: E402
from src.ingest.io import iter_partitions, read_canonical        # noqa: E402
from src.metrics._common import years_to_expiry                  # noqa: E402
from src.metrics.blackscholes import bs_gamma                    # noqa: E402
from src.metrics.flow import option_notional                     # noqa: E402
from src.metrics.gex import contract_gex_recomputed              # noqa: E402


def atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def snapshot_record(df, session, cfg: EngineConfig) -> dict:
    oi = df["open_interest"].astype("float64").fillna(0.0).to_numpy()
    iv = df["iv"].astype("float64").fillna(0.0).to_numpy()
    T = years_to_expiry(df, cfg.pricer.day_count)
    eligible = np.isfinite(iv) & (iv > 0) & (T > 0) & (oi > 0)
    total_oi = float(oi.sum())
    denom = option_notional(df)
    naive = float(contract_gex_recomputed(df, config=cfg).sum())
    otm_cfg = replace(cfg, metrics=replace(cfg.metrics,
                                          dealer_sign_convention="otm_customer"))
    otm = float(contract_gex_recomputed(df, config=otm_cfg).sum())
    spot = df["underlying_price"].astype("float64").to_numpy()
    gamma = bs_gamma(spot, df["strike"].astype("float64").to_numpy(), T, iv,
                     cfg.pricer.risk_free_rate, cfg.pricer.dividend_yield)
    return {
        "date": session.isoformat(), "spot": float(spot[0]),
        "net_gex": naive, "net_gex_otm": otm,
        "gex_norm": naive / denom if denom > 0 else None,
        "option_notional": denom, "n_contracts": int(len(df)),
        "n_iv_eligible_contracts": int(eligible.sum()), "oi_total": total_oi,
        "oi_iv_eligible_fraction": (float(oi[eligible].sum()) / total_oi
                                    if total_oi > 0 else None),
        "max_bs_gamma": float(np.nanmax(gamma)) if len(gamma) else None,
        "gamma_source": "black_scholes_from_valid_stored_iv",
        "pricer_config_hash": cfg.config_hash(),
    }


def rebuild(root: str, symbol: str, out: str, status: str,
            checkpoint_every: int = 100) -> dict:
    cfg = EngineConfig.default()
    parts = list(iter_partitions(root, symbol=symbol))
    records, failures = [], []
    for i, (sym, session, _path) in enumerate(parts, 1):
        try:
            records.append(snapshot_record(read_canonical(root, sym, session), session, cfg))
        except Exception as exc:
            failures.append({"date": session.isoformat(),
                             "error": f"{type(exc).__name__}: {exc}"})
        if i % checkpoint_every == 0 or i == len(parts):
            atomic_json(Path(status), {
                "symbol": symbol.upper(), "processed": i, "total": len(parts),
                "written_records": len(records), "failures": failures,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            print(f"{symbol.upper()} {i}/{len(parts)} records={len(records)} failures={len(failures)}",
                  flush=True)
    atomic_json(Path(out), records)
    return {"symbol": symbol.upper(), "partitions": len(parts), "records": len(records),
            "failures": failures, "out": out}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--status", required=True)
    ap.add_argument("--checkpoint-every", type=int, default=100)
    args = ap.parse_args()
    result = rebuild(args.root, args.symbol, args.out, args.status, args.checkpoint_every)
    print(json.dumps(result, indent=2))
    if result["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
