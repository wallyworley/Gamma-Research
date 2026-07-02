"""Evaluation harness (M5): attribution, cost sweep, and a reproducible scorecard.

Any rule (a target-weight series) can be swapped in and re-scored against
baselines under a cost grid, with returns broken out by gamma regime to see where
any edge actually lives (docs/phase_1_plan.md sections 5.5, 8, 9). Honest
reporting is the point: a rule that does not beat buy-and-hold and the random
control net of costs should be visible as such.
"""

from __future__ import annotations

import pandas as pd

from ..backtest.engine import run_backtest
from ..backtest.stats import buy_and_hold, total_return
from ..config import EngineConfig
from ..metrics._common import resolve_config
from .baselines import random_entry_control

_REGIMES = ("+GEX", "-GEX", "flat")


def regime_attribution(net_equity: pd.Series, regimes: pd.Series) -> dict:
    """Break per-bar strategy returns out by the gamma regime that drove them.

    The return earned over bar t is attributed to the regime observed at bar t-1
    (the signal live when the position was set, given next-open fills). This is a
    documented approximation, not an exact per-trade P&L split.
    """
    rets = net_equity.pct_change().dropna()
    driving = regimes.shift(1).reindex(rets.index)
    out: dict = {}
    for bucket in _REGIMES:
        r = rets[driving == bucket]
        if len(r) == 0:
            out[bucket] = {"n_bars": 0, "mean_return": float("nan"), "total_return": 0.0}
        else:
            out[bucket] = {
                "n_bars": int(len(r)),
                "mean_return": float(r.mean()),
                "total_return": float((1.0 + r).prod() - 1.0),
            }
    return out


def cost_sweep(bars: pd.DataFrame, target_position, *, commissions, slippages_bps,
               config: EngineConfig | None = None) -> pd.DataFrame:
    """Net total return across a grid of commission x slippage assumptions.

    Reports the rule net of costs at each cell so a rosy zero-cost number can't
    hide (section 8 "cost sensitivity"). One row per (commission, slippage) pair.
    """
    base = resolve_config(config).to_dict()
    rows = []
    for commission in commissions:
        for slip in slippages_bps:
            d = {**base, "costs": {
                "commission_per_trade": float(commission),
                "slippage_bps": float(slip),
                "half_spread_cost": base["costs"]["half_spread_cost"],
            }}
            res = run_backtest(bars, target_position, config=EngineConfig.from_dict(d))
            rows.append({
                "commission_per_trade": float(commission),
                "slippage_bps": float(slip),
                "total_return": res.stats["total_return"],
                "final_equity": res.stats["final_equity"],
                "n_trades": res.stats["n_trades"],
            })
    return pd.DataFrame(rows)


def scorecard(bars: pd.DataFrame, target_position, *, config: EngineConfig | None = None,
              regimes: pd.Series | None = None, random_seed: int = 0,
              random_prob: float = 0.5) -> dict:
    """One reproducible scorecard: rule stats vs baselines, plus attribution.

    Stamped with EngineConfig.config_hash() so a result is tied to the exact
    pricer/cost/convention assumptions that produced it.
    """
    cfg = resolve_config(config)
    result = run_backtest(bars, target_position, config=cfg)

    bh = buy_and_hold(bars, cfg.backtest.initial_capital)
    rand = run_backtest(bars, random_entry_control(bars, seed=random_seed, prob=random_prob),
                        config=cfg)

    card = {
        "config_hash": cfg.config_hash(),
        "strategy": result.stats,
        "buy_and_hold_return": total_return(bh),
        "random_entry_return": rand.stats["total_return"],
        "beats_buy_and_hold": result.stats["total_return"] > total_return(bh),
        "beats_random_entry": result.stats["total_return"] > rand.stats["total_return"],
    }
    if regimes is not None:
        card["regime_attribution"] = regime_attribution(result.net_equity, regimes)
    return card


__all__ = ["regime_attribution", "cost_sweep", "scorecard"]
