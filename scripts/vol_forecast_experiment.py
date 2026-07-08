"""The FIRST REAL EXPERIMENT (quant review item 3 / F4, with the F11 sweep).

Question: does end-of-day normalized Net GEX add next-day realized-volatility
forecast value BEYOND vol clustering (a HAR baseline)?

Inputs
------
* --series : a precomputed per-session GEX series JSON (built on the VPS from the
  ThetaData backfill + nightly Massive store; one record per session with
  ``net_gex`` (long_call_short_put), ``net_gex_otm`` (otm_customer) and
  ``option_notional``). The signal on date t is computed from that session's own
  EOD chain, so it is known at the close of t - the alignment contract
  ``eval.volatility.vol_forecast_scorecard`` requires.
* --bars : a daily OHLC CSV (yfinance ``auto_adjust=False`` format: Date index,
  Open/High/Low/Close columns). Validated against Massive aggregates (SPY overlap
  agrees to ~1e-5), the official Cboe SPX close history, and the vendor closes
  embedded in the series itself (median |rel diff| ~2e-8) before first use.

F11 (dealer-sign sensitivity): the dealer sign is unobservable, so the experiment
runs once per convention - the signal is REBUILT from the convention's own net-GEX
column (``otm_customer`` is a per-contract re-signing, not a global sign flip) and
scored under a config whose ``dealer_sign_convention`` matches, so each scorecard's
config_hash records its assumption. The verdict mirrors eval.sensitivity: a
conclusion that holds under one convention and not the other (or with an
incompatible coefficient sign) is flagged ``flips`` and cannot be trusted.

"Adds value" is deliberately strict: incremental ADJUSTED R2 > 0 AND the
moving-block-bootstrap fraction of resamples with increment <= 0 at most 0.05.

Run (from the repo root, venv active):

    python scripts/vol_forecast_experiment.py \
        --series data/analysis/gex_series_SPX.json \
        --bars data/analysis/yf_spx_daily.csv \
        --label SPX --out data/analysis/vol_forecast_results_SPX.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import EngineConfig                      # noqa: E402
from src.eval.volatility import vol_forecast_scorecard   # noqa: E402

# Convention -> the series column holding Net GEX under that convention.
CONVENTION_COLUMNS = {
    "long_call_short_put": "net_gex",
    "otm_customer": "net_gex_otm",
}
TARGETS = ("range", "abs_return", "parkinson")
MAX_FRAC_LE_0 = 0.05     # bootstrap honesty gate for "adds value"


def load_series(path: str) -> pd.DataFrame:
    """Per-session GEX series JSON -> DataFrame indexed by session date."""
    records = json.loads(Path(path).read_text())
    df = pd.DataFrame(records)
    df.index = pd.to_datetime(df["date"])
    df = df.drop(columns=["date"]).sort_index()
    if df.index.has_duplicates:
        raise ValueError(f"duplicate sessions in {path}")
    return df


def load_bars(path: str) -> pd.DataFrame:
    """yfinance daily CSV -> bars frame (lowercase ohlc, sorted DatetimeIndex)."""
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df = df.rename(columns={c: c.lower() for c in df.columns})
    bars = df[["open", "high", "low", "close"]].astype("float64").sort_index()
    if bars.index.has_duplicates:
        raise ValueError(f"duplicate bar dates in {path}")
    return bars


def normalized_signal(series: pd.DataFrame, convention: str) -> pd.Series:
    """Normalized Net GEX under ``convention``: net_gex_<conv> / option_notional.

    option_notional is sign-convention independent, so normalization does not
    touch the convention question. Non-positive/missing notional -> NaN (the
    scorecard drops those rows), matching metrics.flow.gex_normalized's refusal
    to emit an undefined ratio.
    """
    col = CONVENTION_COLUMNS[convention]
    notional = series["option_notional"].astype("float64")
    ok = notional > 0
    return series[col].astype("float64").where(ok) / notional.where(ok)


def adds_value(card: dict) -> bool | None:
    """Strict conclusion: positive adjusted-R2 increment AND bootstrap-solid."""
    inc = card["incremental_r2_adj"]
    frac = card["bootstrap"]["frac_incremental_le_0"]
    if not (isinstance(inc, float) and math.isfinite(inc) and math.isfinite(frac)):
        return None
    return inc > 0 and frac <= MAX_FRAC_LE_0


def run(series_path: str, bars_path: str, label: str,
        n_bootstrap: int, seed: int) -> dict:
    series = load_series(series_path)
    bars = load_bars(bars_path)

    # The stored gex_norm was computed under the default convention; assert our
    # rebuilt signal matches it so the JSON and this script can't silently drift.
    if "gex_norm" in series.columns:
        rebuilt = normalized_signal(series, "long_call_short_put")
        stored = series["gex_norm"].astype("float64")
        both = pd.concat([rebuilt, stored], axis=1).dropna()
        if len(both) and not (both.iloc[:, 0] - both.iloc[:, 1]).abs().max() < 1e-12:
            raise AssertionError("rebuilt gex_norm disagrees with the stored series")

    overlap = series.index.intersection(bars.index)
    results: dict = {
        "label": label,
        "series": str(series_path),
        "bars": str(bars_path),
        "sessions_in_series": int(len(series)),
        "sessions_with_bars": int(len(overlap)),
        "span": [str(overlap.min().date()), str(overlap.max().date())] if len(overlap) else None,
        "targets": {},
    }

    base_cfg = EngineConfig()
    for target in TARGETS:
        per_conv: dict = {}
        for conv in CONVENTION_COLUMNS:
            cfg = replace(base_cfg, metrics=replace(base_cfg.metrics,
                                                    dealer_sign_convention=conv))
            signal = normalized_signal(series, conv)
            card = vol_forecast_scorecard(bars, signal, config=cfg, target=target,
                                          n_bootstrap=n_bootstrap, seed=seed)
            card["adds_value"] = adds_value(card)
            per_conv[conv] = card

        verdicts = {c: card["adds_value"] for c, card in per_conv.items()}
        distinct = {v for v in verdicts.values() if v is not None}
        results["targets"][target] = {
            "per_convention": per_conv,
            "f11_verdict": {
                # A conclusion that holds under one dealer-sign assumption but not
                # the other rides on the unobservable convention: dead on arrival.
                "flips": len(distinct) > 1,
                "adds_value_by_convention": verdicts,
            },
        }
    return results


def summarize(results: dict) -> str:
    lines = [f"== {results['label']}  ({results['sessions_with_bars']} sessions "
             f"{results['span'][0]} .. {results['span'][1]}) =="]
    for target, block in results["targets"].items():
        lines.append(f"  target={target}")
        for conv, card in block["per_convention"].items():
            b = card["bootstrap"]
            lines.append(
                f"    {conv:<22} n={card['n_obs']:<5} HAR_R2={card['baseline_r2']:.4f} "
                f"inc_adjR2={card['incremental_r2_adj']:+.5f} "
                f"coef={card['signal_coef']:+.3e} t={card['signal_tstat']:+.2f} "
                f"boot_frac<=0={b['frac_incremental_le_0']:.3f} "
                f"CI95=({b['incremental_r2_adj_ci95'][0]:+.5f},{b['incremental_r2_adj_ci95'][1]:+.5f}) "
                f"adds_value={card['adds_value']}")
        v = block["f11_verdict"]
        lines.append(f"    F11: flips={v['flips']}  {v['adds_value_by_convention']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--series", required=True)
    ap.add_argument("--bars", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--n-bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    results = run(args.series, args.bars, args.label, args.n_bootstrap, args.seed)
    print(summarize(results))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, default=str))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
