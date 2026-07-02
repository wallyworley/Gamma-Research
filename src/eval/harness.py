"""Evaluation harness (M5): attribution, cost sweep, and a reproducible scorecard.

Any rule (a target-weight series) can be swapped in and re-scored against
baselines under a cost grid, with returns broken out by gamma regime to see where
any edge actually lives (docs/phase_1_plan.md sections 5.5, 8, 9). Honest
reporting is the point: a rule that does not beat buy-and-hold and the random
control net of costs should be visible as such.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest.engine import run_backtest
from ..backtest.stats import buy_and_hold, total_return
from ..config import EngineConfig
from ..metrics._common import resolve_config
from .baselines import permutation_control, random_entry_control

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
                "half_spread_bps": base["costs"]["half_spread_bps"],
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


def _exposure_fraction(target_position, bars: pd.DataFrame) -> float:
    """Realized average absolute exposure of a target-weight series, in [0, 1].

    Forward-fills (a missing target means hold) and averages |weight| over bars.
    This is what the random control is matched to, so beating it requires *timing*,
    not just being in the market.
    """
    w = pd.Series(target_position).reindex(bars.index).ffill().fillna(0.0).abs()
    return float(min(max(w.mean(), 0.0), 1.0))


def _sharpe(bar_returns: pd.Series, periods_per_year: int = 252) -> float:
    r = bar_returns.dropna()
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return float("nan") if len(r) < 2 or sd == 0 else float(r.mean() / sd * np.sqrt(periods_per_year))


def _bootstrap_mean_ci(bar_returns: pd.Series, *, seed: int, n: int,
                       alpha: float = 0.05) -> tuple[float, float]:
    r = bar_returns.dropna().to_numpy()
    if r.size == 0 or n <= 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(r, size=(n, r.size), replace=True).mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2.0, 1.0 - alpha / 2.0])
    return (float(lo), float(hi))


def _percentile_beaten(strat_ret: float, control_returns: np.ndarray) -> float:
    """Fraction of controls the strategy strictly beats (NaN if no controls)."""
    return float(np.mean(strat_ret > control_returns)) if control_returns.size else float("nan")


def scorecard(bars: pd.DataFrame, target_position, *, config: EngineConfig | None = None,
              regimes: pd.Series | None = None, n_permutations: int = 1000,
              n_controls: int = 1000, random_seed: int = 0, bootstrap_n: int = 1000) -> dict:
    """A reproducible scorecard: rule vs sign-safe permutations + baselines, with CIs.

    Replaces the old naked `beats_*` booleans (which an informationless always-in
    signal passed, review finding F3). The **primary** timing test is
    `permutation_test`: the strategy vs shuffles of its OWN weights, which match
    exposure, sign (long AND short), and turnover, so `strategy_percentile` there
    measures timing skill and cannot be fooled by beta of either sign. A secondary
    exposure-matched (long-only) random control, a bootstrap CI on the mean bar
    return, and a Sharpe are also reported. Stamped with config_hash().
    """
    cfg = resolve_config(config)
    result = run_backtest(bars, target_position, config=cfg)
    strat_ret = result.stats["total_return"]
    bar_returns = result.net_equity.pct_change()

    bh_ret = total_return(buy_and_hold(bars, cfg.backtest.initial_capital))
    exposure = _exposure_fraction(target_position, bars)

    # Primary: permutation test of timing skill (sign/exposure/turnover-matched).
    perms = np.array([
        run_backtest(bars, permutation_control(target_position, bars, seed=random_seed + k),
                     config=cfg).stats["total_return"]
        for k in range(n_permutations)
    ]) if n_permutations > 0 else np.array([])

    # Secondary: exposure-matched random long/flat control (directional baseline).
    controls = np.array([
        run_backtest(bars, random_entry_control(bars, seed=10_000 + random_seed + k, prob=exposure),
                     config=cfg).stats["total_return"]
        for k in range(n_controls)
    ]) if n_controls > 0 else np.array([])

    card = {
        "config_hash": cfg.config_hash(),
        "strategy": result.stats,
        "strategy_sharpe": _sharpe(bar_returns),
        "strategy_mean_bar_return": (float(bar_returns.dropna().mean())
                                     if not bar_returns.dropna().empty else float("nan")),
        "bootstrap_mean_ci_95": _bootstrap_mean_ci(bar_returns, seed=random_seed, n=bootstrap_n),
        "buy_and_hold_return": bh_ret,
        "excess_vs_buy_and_hold": strat_ret - bh_ret,
        "permutation_test": {
            "n": int(n_permutations),
            "strategy_percentile": _percentile_beaten(strat_ret, perms),
            "mean_return": float(perms.mean()) if perms.size else float("nan"),
        },
        "random_control": {
            "n": int(n_controls),
            "exposure_matched_prob": exposure,
            "note": "long-only; use permutation_test for sign-safe timing",
            "mean_return": float(controls.mean()) if controls.size else float("nan"),
            "median_return": float(np.median(controls)) if controls.size else float("nan"),
            "strategy_percentile": _percentile_beaten(strat_ret, controls),
        },
    }
    if regimes is not None:
        card["regime_attribution"] = regime_attribution(result.net_equity, regimes)
    return card


__all__ = ["regime_attribution", "cost_sweep", "scorecard"]
