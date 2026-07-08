#!/usr/bin/env python3
"""Empirical dealer-sign calibration pipeline (Route 2): pull -> map -> scorecard.

Replaces the unobservable dealer-position ASSUMPTION with a MEASUREMENT for SPY, then
reruns the next-day realized-vol forecast scorecard as a third arm beside the two
sign conventions. Four subcommands (run in order; each is resumable / re-runnable):

  pull       Select sampled sessions (~per-month + top-|gex_norm|) and cache each
             session's ThetaData trades+NBBO to parquet. Resumable: cached sessions
             are skipped, per-session failures isolated, a rate-limit wall stops it.
  map        Classify every cached session (quote rule), aggregate signed customer
             flow into type x moneyness x DTE buckets, build the dealer-sign map
             (dealer = -sign of mean net customer flow) with two-halves stability,
             then rebuild the empirical-sign Net GEX series over the whole store
             (full map + early-only + late-only for the out-of-sample split). Also
             runs the condition-robustness and OI-reconciliation diagnostics.
  scorecard  Run vol_forecast_scorecard on all three arms (long_call_short_put,
             otm_customer, empirical) x three targets on the same bars + gate, plus
             the out-of-sample split cells (early map -> late bars, and the reverse).
  all        pull then map then scorecard.

Example:
    python scripts/calibrate_dealer_sign.py all --symbol SPY \
        --series data/analysis/gex_series_SPY.json \
        --bars data/analysis/yf_spy_daily.csv \
        --store ~/Backups/gamma-research/data \
        --root data/calibration --start 2017-01-01 --end 2026-06-30
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.calibration import aggregate, gex_rebuild, pull, signmap  # noqa: E402
from src.calibration.signmap import DEFAULT_SPLIT_DATE, stable_sign_lookup  # noqa: E402
from src.config import EngineConfig  # noqa: E402
from src.ingest import io as _io  # noqa: E402

_log = logging.getLogger("calibrate")

TARGETS = ("range", "abs_return", "parkinson")
MAX_FRAC_LE_0 = 0.05          # bootstrap honesty gate (same as the experiment)
CONVENTION_COLUMNS = {"long_call_short_put": "net_gex", "otm_customer": "net_gex_otm"}
# Sub-splits used to gauge stability WITHIN each half for the out-of-sample maps.
EARLY_SUBSPLIT = "2019-07-01"
LATE_SUBSPLIT = "2024-01-01"


# --------------------------------------------------------------------------- #
# Session selection
# --------------------------------------------------------------------------- #

def _target_days(per_month: int) -> list[int]:
    """Evenly-spaced target days-of-month for per-month sampling (2 -> [10, 20])."""
    return [max(1, round((i + 1) * 30 / (per_month + 1))) for i in range(per_month)]


def select_sessions(series_path: str, start: str, end: str, *,
                    per_month: int = 2, top_gex: int = 20) -> dict:
    """Sampled sessions: ~per_month per month nearest fixed target days, PLUS the
    top-|gex_norm| sessions. Returns {sessions, monthly, top_gex, split_date}."""
    recs = json.loads(Path(series_path).read_text())
    inrange = [(r["date"], float(r["gex_norm"])) for r in recs
               if r.get("gex_norm") is not None and start <= r["date"] <= end]
    by_month: dict[str, list[str]] = defaultdict(list)
    for d, _ in inrange:
        by_month[d[:7]].append(d)
    monthly: set[str] = set()
    for _, ds in by_month.items():
        ds.sort()
        for tgt in _target_days(per_month):
            monthly.add(min(ds, key=lambda x: abs(int(x[8:10]) - tgt)))
    topg = {d for d, _ in sorted(inrange, key=lambda x: abs(x[1]), reverse=True)[:top_gex]}
    return {"sessions": sorted(monthly | topg), "monthly": sorted(monthly),
            "top_gex": sorted(topg), "split_date": DEFAULT_SPLIT_DATE}


def _session_list_path(root: str, symbol: str) -> str:
    return os.path.join(root, f"session_list_{symbol.upper()}.json")


# --------------------------------------------------------------------------- #
# pull
# --------------------------------------------------------------------------- #

def cmd_pull(args) -> int:
    sel = select_sessions(args.series, args.start, args.end,
                          per_month=args.per_month, top_gex=args.top_gex)
    os.makedirs(args.root, exist_ok=True)
    Path(_session_list_path(args.root, args.symbol)).write_text(json.dumps(sel, indent=1))
    sessions = sel["sessions"]
    _log.info("selected %d sessions (%d monthly + %d top-gex) %s..%s",
              len(sessions), len(sel["monthly"]), len(sel["top_gex"]), args.start, args.end)

    client = pull.make_client()
    counts = {"written": 0, "empty": 0, "cached": 0, "failed": 0}
    t0 = time.time()
    for i, sess in enumerate(sessions, 1):
        try:
            status, n = pull.cache_session(client, args.symbol, sess, args.root,
                                           max_dte=args.max_dte)
            counts[status] = counts.get(status, 0) + 1
            if status != "cached":
                _log.info("[%d/%d] %s %s rows=%d (%.0fs elapsed)",
                          i, len(sessions), sess, status, n, time.time() - t0)
        except pull.RateLimited as e:
            _log.error("RATE-LIMIT/ENTITLEMENT WALL at %s: %s -- stopping (resume later)", sess, e)
            break
        except Exception as e:  # noqa: BLE001 - isolate one bad session
            counts["failed"] += 1
            _log.warning("[%d/%d] FAILED %s: %s: %s", i, len(sessions), sess,
                         type(e).__name__, e)
    _log.info("pull done: %s in %.0fs -> %s", counts, time.time() - t0, args.root)
    return 0


# --------------------------------------------------------------------------- #
# map
# --------------------------------------------------------------------------- #

def _collect_flows(args) -> tuple[list[dict], list[dict]]:
    """Classify + aggregate every cached sampled session. Returns (flow_records,
    session_stats). flow_records feed build_sign_map; each carries net_flow,
    net_flow_reg (regular-condition only), net_flow_gamma, total_size, counts."""
    sel = json.loads(Path(_session_list_path(args.root, args.symbol)).read_text())
    flows: list[dict] = []
    stats: list[dict] = []
    for sess in sel["sessions"]:
        if not pull.is_cached(args.root, args.symbol, sess):
            continue
        spot, glk = aggregate.load_spot_and_gamma(args.store, args.symbol, sess)
        if spot is None:
            _log.warning("no stored chain for %s; skipping (no spot)", sess)
            continue
        trades = pull.read_cached(args.root, args.symbol, sess)
        cl = aggregate.classify_session_trades(trades, spot, sess, gamma_lookup=glk)
        if len(cl) == 0:
            continue
        flows.extend(aggregate.aggregate_session(cl, sess))
        stats.append({"session": sess, "spot": spot, **aggregate.session_stats(cl)})
    return flows, stats


def _remap(records: list[dict], field: str) -> list[dict]:
    """Copy flow records with net_flow replaced by another flow field (reg/gamma)."""
    return [{**r, "net_flow": r[field]} for r in records]


def _oi_sanity_check(args, sessions: list[str], max_sessions: int = 12) -> dict:
    """Correlate per-contract net classified flow on S with OI change S -> next stored
    session (a classification-quality diagnostic, NOT a gate). Pooled over a subsample."""
    import numpy as np
    import pandas as pd

    all_dates = [d for _, d, _ in _io.iter_partitions(args.store, symbol=args.symbol)]
    date_set = set(all_dates)
    picks = sessions[:: max(1, len(sessions) // max_sessions)][:max_sessions]

    flow_vals: list[float] = []
    doi_vals: list[float] = []
    used = 0
    for sess in picks:
        if not pull.is_cached(args.root, args.symbol, sess):
            continue
        d = date.fromisoformat(sess)
        nxt = next((x for x in all_dates if x > d), None)
        if nxt is None or d not in date_set:
            continue
        spot, glk = aggregate.load_spot_and_gamma(args.store, args.symbol, sess)
        if spot is None:
            continue
        cl = aggregate.classify_session_trades(
            pull.read_cached(args.root, args.symbol, sess), spot, sess, gamma_lookup=glk)
        if len(cl) == 0:
            continue
        dirn = cl[cl["customer_sign"] != 0].copy()
        dirn["exp"] = pd.to_datetime(dirn["expiration"]).dt.strftime("%Y-%m-%d")
        dirn["signed"] = dirn["size"].astype("float64") * dirn["customer_sign"]
        flow = dirn.groupby(["exp", "strike", "type"])["signed"].sum()

        ch0 = _io.read_canonical(args.store, args.symbol, d)
        ch1 = _io.read_canonical(args.store, args.symbol, nxt)

        def oi_map(ch):
            g = ch[["expiration", "strike", "type", "open_interest"]].copy()
            g["exp"] = g["expiration"].dt.strftime("%Y-%m-%d")
            g["strike"] = g["strike"].astype("float64")
            g["oi"] = g["open_interest"].astype("float64").fillna(0.0)
            return g.groupby(["exp", "strike", "type"])["oi"].sum()

        oi0, oi1 = oi_map(ch0), oi_map(ch1)
        doi = (oi1 - oi0).dropna()
        common = flow.index.intersection(doi.index)
        if len(common) < 20:
            continue
        flow_vals.extend(flow.loc[common].to_numpy())
        doi_vals.extend(doi.loc[common].to_numpy())
        used += 1

    if len(flow_vals) < 50:
        return {"n_sessions": used, "n_contracts": len(flow_vals), "pearson_r": None}
    fv, dv = np.array(flow_vals), np.array(doi_vals)
    r = float(np.corrcoef(fv, dv)[0, 1]) if fv.std() > 0 and dv.std() > 0 else None
    return {"n_sessions": used, "n_contracts": len(fv), "pearson_r": r,
            "note": "classified net flow vs next-session OI change; open/close "
                    "unobservable and exchange coverage differs, so <1 correlation expected"}


def _build_empirical_series(args, lookups: dict) -> tuple[list[dict], dict]:
    """One pass over the store: per-session empirical Net GEX under each lookup, plus a
    pooled gamma-weighted convention-agreement summary under the full map."""
    import numpy as np

    conv_num = defaultdict(float)  # pooled gamma*OI-weighted matched weight
    conv_den = 0.0
    series: list[dict] = []
    parts = list(_io.iter_partitions(args.store, symbol=args.symbol))
    for k, (_, d, _) in enumerate(parts, 1):
        chain = _io.read_canonical(args.store, args.symbol, d)
        rec = {"date": d.isoformat()}
        for name, lk in lookups.items():
            res = gex_rebuild.empirical_net_gex(chain, lk)
            rec[f"net_gex_{name}"] = res["net_gex"]
            if name == "full":
                rec["spot"] = res["spot"]
                rec["option_notional"] = res["option_notional"]
                rec["fallback_gamma_oi_frac"] = res["fallback_gamma_oi_frac"]
        # pooled agreement under the full map (weight by stable gamma*OI per session)
        agr = gex_rebuild.convention_agreement(chain, lookups["full"])
        spot = chain["underlying_price"].astype("float64").to_numpy()
        gamma = chain["gamma"].astype("float64").fillna(0.0).to_numpy()
        oi = chain["open_interest"].astype("float64").fillna(0.0).to_numpy()
        w = float(np.sum(np.abs(gamma * oi))) * agr["stable_gamma_oi_frac"]
        conv_den += w
        for c in CONVENTION_COLUMNS:
            conv_num[c] += w * (agr[c] if agr[c] == agr[c] else 0.0)  # skip nan
        series.append(rec)
        if k % 400 == 0:
            _log.info("empirical series %d/%d sessions", k, len(parts))
    agreement = {c: (conv_num[c] / conv_den if conv_den > 0 else None) for c in CONVENTION_COLUMNS}
    return series, agreement


def cmd_map(args) -> int:
    import numpy as np  # noqa: F401

    flows, stats = _collect_flows(args)
    if not flows:
        _log.error("no flow records; run `pull` first")
        return 1
    _log.info("collected %d bucket-session flow records over %d sessions",
              len(flows), len(stats))

    early = [r for r in flows if r["session"] < DEFAULT_SPLIT_DATE]
    late = [r for r in flows if r["session"] >= DEFAULT_SPLIT_DATE]

    full_map = signmap.build_sign_map(flows)
    regular_map = signmap.build_sign_map(_remap(flows, "net_flow_reg"))
    gamma_map = signmap.build_sign_map(_remap(flows, "net_flow_gamma"))
    early_map = signmap.build_sign_map(early, split_date=EARLY_SUBSPLIT)
    late_map = signmap.build_sign_map(late, split_date=LATE_SUBSPLIT)

    lookups = {
        "full": stable_sign_lookup(full_map),
        "early": stable_sign_lookup(early_map),
        "late": stable_sign_lookup(late_map),
    }
    _log.info("stable buckets: full=%d early=%d late=%d",
              len(lookups["full"]), len(lookups["early"]), len(lookups["late"]))

    # Robustness: do stable buckets keep their sign under the regular-only map?
    robustness = {}
    for b, s in full_map.items():
        if s["stable"]:
            reg = regular_map.get(b, {})
            robustness[b] = {
                "primary_sign": s["dealer_sign"],
                "regular_sign": reg.get("dealer_sign"),
                "regular_stable": reg.get("stable", False),
                "agrees": reg.get("dealer_sign") == s["dealer_sign"],
                "gamma_sign": gamma_map.get(b, {}).get("dealer_sign"),
            }

    series, agreement = _build_empirical_series(args, lookups)
    oi_check = _oi_sanity_check(args, [s["session"] for s in stats])

    out = {
        "symbol": args.symbol,
        "n_flow_records": len(flows),
        "n_sessions_sampled": len(stats),
        "split_date": DEFAULT_SPLIT_DATE,
        "sign_map": full_map,
        "regular_map": regular_map,
        "gamma_map": gamma_map,
        "early_map": early_map,
        "late_map": late_map,
        "stable_counts": {k: len(v) for k, v in lookups.items()},
        "robustness_condition": robustness,
        "convention_agreement_gamma_oi_weighted": agreement,
        "oi_sanity_check": oi_check,
        "session_stats": stats,
    }
    map_path = os.path.join(args.root, f"sign_map_{args.symbol}.json")
    Path(map_path).write_text(json.dumps(out, indent=2, default=str))
    series_path = os.path.join(args.root, f"empirical_series_{args.symbol}.json")
    Path(series_path).write_text(json.dumps(series, indent=1, default=str))
    _log.info("wrote %s and %s", map_path, series_path)
    _print_map_summary(out)
    return 0


def _print_map_summary(out: dict) -> None:
    print(f"\n== dealer-sign map {out['symbol']}  "
          f"({out['n_sessions_sampled']} sampled sessions, "
          f"{out['stable_counts']['full']}/30 stable buckets) ==")
    print(f"convention agreement (gamma*OI-weighted over stable cells): "
          f"{ {k: (round(v,3) if v is not None else None) for k,v in out['convention_agreement_gamma_oi_weighted'].items()} }")
    oc = out["oi_sanity_check"]
    print(f"OI reconciliation: r={oc.get('pearson_r')} over {oc.get('n_contracts')} "
          f"contract-days / {oc.get('n_sessions')} sessions")
    print("  bucket                       dealer  t_stat   n   early/late  stable  reg_agrees")
    for b in sorted(out["sign_map"]):
        s = out["sign_map"][b]
        rob = out["robustness_condition"].get(b, {})
        t = s["t_stat"]
        tstr = f"{t:+.1f}" if isinstance(t, (int, float)) and math.isfinite(t) else str(t)
        print(f"  {b:<28} {s['dealer_sign']:+d}    {tstr:>6}  {s['n_sessions']:>3}  "
              f"{s['early_sign']:+d}/{s['late_sign']:+d}     {str(s['stable']):<5}  "
              f"{rob.get('agrees', '-')}")


# --------------------------------------------------------------------------- #
# scorecard
# --------------------------------------------------------------------------- #

def _load_bars(path: str):
    import pandas as pd

    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df = df.rename(columns={c: c.lower() for c in df.columns})
    bars = df[["open", "high", "low", "close"]].astype("float64").sort_index()
    return bars


def _series_df(path: str):
    import pandas as pd

    df = pd.DataFrame(json.loads(Path(path).read_text()))
    df.index = pd.to_datetime(df["date"])
    return df.drop(columns=["date"]).sort_index()


def _norm(numer, denom):
    import numpy as np

    d = denom.astype("float64")
    ok = d > 0
    return numer.astype("float64").where(ok) / d.where(ok)


def _adds_value(card: dict):
    inc = card["incremental_r2_adj"]
    frac = card["bootstrap"]["frac_incremental_le_0"]
    if not (isinstance(inc, float) and math.isfinite(inc) and math.isfinite(frac)):
        return None
    return inc > 0 and frac <= MAX_FRAC_LE_0


def _score(bars, signal, conv_label, target, args):
    from src.eval.volatility import vol_forecast_scorecard

    base = EngineConfig()
    cfg = replace(base, metrics=replace(base.metrics, dealer_sign_convention=conv_label))
    card = vol_forecast_scorecard(bars, signal, config=cfg, target=target,
                                  n_bootstrap=args.n_bootstrap, seed=args.seed)
    card["adds_value"] = _adds_value(card)
    return card


def cmd_scorecard(args) -> int:
    conv_series = _series_df(args.series)                                   # two conventions
    emp = _series_df(os.path.join(args.root, f"empirical_series_{args.symbol}.json"))
    bars = _load_bars(args.bars)

    signals = {
        "long_call_short_put": _norm(conv_series["net_gex"], conv_series["option_notional"]),
        "otm_customer": _norm(conv_series["net_gex_otm"], conv_series["option_notional"]),
        "empirical": _norm(emp["net_gex_full"], emp["option_notional"]),
    }
    # Out-of-sample split signals (map estimated on one half, scored on the other).
    oos = {
        "empirical_earlymap": _norm(emp["net_gex_early"], emp["option_notional"]),
        "empirical_latemap": _norm(emp["net_gex_late"], emp["option_notional"]),
    }
    late_bars = bars[bars.index >= DEFAULT_SPLIT_DATE]
    early_bars = bars[bars.index < DEFAULT_SPLIT_DATE]

    results: dict = {"symbol": args.symbol, "bars": args.bars, "gate": {
        "incremental_r2_adj>0": True, "frac_incremental_le_0<=": MAX_FRAC_LE_0}, "targets": {}}
    for target in TARGETS:
        block = {"arms": {}, "oos": {}}
        for label, sig in signals.items():
            block["arms"][label] = _score(bars, sig, label, target, args)
        # OOS: early map -> late bars ; late map -> early bars
        block["oos"]["earlymap_on_late_bars"] = _score(
            late_bars, oos["empirical_earlymap"], "empirical_earlymap", target, args)
        block["oos"]["latemap_on_early_bars"] = _score(
            early_bars, oos["empirical_latemap"], "empirical_latemap", target, args)
        results["targets"][target] = block

    out_path = args.out or os.path.join(args.root, f"scorecard_{args.symbol}.json")
    Path(out_path).write_text(json.dumps(results, indent=2, default=str))
    _print_scorecard(results)
    print(f"\nwrote {out_path}")
    return 0


def _fmt_card(label, c):
    b = c["bootstrap"]
    return (f"    {label:<26} n={c['n_obs']:<5} HAR_R2={c['baseline_r2']:.3f} "
            f"inc_adjR2={c['incremental_r2_adj']:+.5f} coef={c['signal_coef']:+.2e} "
            f"t={c['signal_tstat']:+.2f} boot<=0={b['frac_incremental_le_0']:.3f} "
            f"adds_value={c['adds_value']}")


def _print_scorecard(results: dict) -> None:
    print(f"\n== three-arm vol-forecast scorecard {results['symbol']} ==")
    for target, block in results["targets"].items():
        print(f"  target={target}")
        for label, c in block["arms"].items():
            print(_fmt_card(label, c))
        for label, c in block["oos"].items():
            print(_fmt_card("OOS:" + label, c))


# --------------------------------------------------------------------------- #

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["pull", "map", "scorecard", "all"])
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--series", default="data/analysis/gex_series_SPY.json")
    ap.add_argument("--bars", default="data/analysis/yf_spy_daily.csv")
    ap.add_argument("--store", default=os.path.expanduser("~/Backups/gamma-research/data"))
    ap.add_argument("--root", default="data/calibration")
    ap.add_argument("--start", default="2017-01-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--per-month", type=int, default=2)
    ap.add_argument("--top-gex", type=int, default=20)
    ap.add_argument("--max-dte", type=int, default=60)
    ap.add_argument("--n-bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "pull":
        return cmd_pull(args)
    if args.command == "map":
        return cmd_map(args)
    if args.command == "scorecard":
        return cmd_scorecard(args)
    rc = cmd_pull(args) or cmd_map(args) or cmd_scorecard(args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
