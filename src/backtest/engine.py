"""Event-driven backtester on the underlying (M4).

Non-live, simulated fills only (docs/phase_1_plan.md sections 2 and 7). The engine
is deliberately signal-agnostic: it consumes a bar timeline and a target-position
series and simulates the fills, costs, and PnL. Where the target comes from
(a gamma-structure rule) is the signal layer's job.

Point-in-time correctness is the whole game here:

    target_position[t] is the position DECIDED using information available at the
    CLOSE of bar t. It is EXECUTED at the OPEN of bar t+1.

So a signal computed from bar t's EOD chain can never fill at bar t's own close;
it fills one bar later. `backtest.allow_same_bar_fill` (default False, pinned in
M0) flips this to same-close execution, kept only for measuring the look-ahead a
naive fill would have stolen.

Costs come from CostConfig: a flat commission per rebalance plus slippage in bps
on traded notional. Results are reported both net and gross so cost drag is
explicit (section 8 "cost sensitivity").
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config import EngineConfig
from ..metrics._common import resolve_config

_EPS_SHARES = 1e-9


def validate_bars(bars: pd.DataFrame) -> None:
    """Ensure ``bars`` is a clean OHLC-ish timeline: datetime index, open/close > 0."""
    issues = []
    if not isinstance(bars.index, pd.DatetimeIndex):
        issues.append("index must be a DatetimeIndex")
    elif not bars.index.is_monotonic_increasing:
        issues.append("index must be sorted ascending")
    elif not bars.index.is_unique:
        issues.append("index must be unique (one bar per timestamp)")
    for col in ("open", "close"):
        if col not in bars.columns:
            issues.append(f"missing required column {col!r}")
        elif (bars[col] <= 0).any() or bars[col].isna().any():
            issues.append(f"column {col!r} must be positive and non-null")
    if issues:
        raise ValueError("invalid bars: " + "; ".join(issues))


@dataclass(frozen=True)
class BacktestResult:
    """Outcome of one backtest run."""

    net_equity: pd.Series      # mark-to-market equity after costs
    gross_equity: pd.Series    # same path with costs zeroed (cost-drag reference)
    trades: pd.DataFrame       # one row per executed rebalance
    stats: dict                # summary metrics (see stats.summarize)


def _simulate(bars: pd.DataFrame, target: pd.Series, cfg: EngineConfig,
              *, apply_costs: bool) -> tuple[pd.Series, pd.DataFrame]:
    timestamps = list(bars.index)
    same_bar = cfg.backtest.allow_same_bar_fill
    commission = cfg.costs.commission_per_trade if apply_costs else 0.0
    slip = (cfg.costs.slippage_bps / 1e4) if apply_costs else 0.0

    cash = float(cfg.backtest.initial_capital)
    shares = 0.0
    equity: dict = {}
    trades: list[dict] = []

    for i, t in enumerate(timestamps):
        open_px = float(bars.at[t, "open"])
        close_px = float(bars.at[t, "close"])

        # Which target executes here, and at what price.
        if same_bar:
            tgt = target.get(t)          # decided and filled at this close (look-ahead mode)
            fill_px = close_px
        else:
            tgt = target.get(timestamps[i - 1]) if i > 0 else None  # decided at t-1 close
            fill_px = open_px            # ...filled at this open

        if tgt is not None and not pd.isna(tgt):
            equity_at_fill = cash + shares * fill_px
            target_shares = float(tgt) * equity_at_fill / fill_px
            delta = target_shares - shares
            if abs(delta) > _EPS_SHARES:
                cost = commission + slip * abs(delta) * fill_px
                cash -= delta * fill_px + cost
                shares = target_shares
                trades.append({
                    "ts": t, "fill_price": fill_px, "delta_shares": delta,
                    "shares_after": shares, "cost": cost, "target_weight": float(tgt),
                })

        equity[t] = cash + shares * close_px

    eq = pd.Series(equity).sort_index()
    trade_df = pd.DataFrame(trades, columns=[
        "ts", "fill_price", "delta_shares", "shares_after", "cost", "target_weight"])
    return eq, trade_df


def run_backtest(bars: pd.DataFrame, target_position, *,
                 config: EngineConfig | None = None) -> BacktestResult:
    """Simulate trading the underlying to ``target_position`` over ``bars``.

    ``target_position`` is a per-bar target weight in [-1, 1] (fraction of equity;
    negative = short), indexed by bar timestamp, where the value at bar t is the
    position decided at t's close. A missing/NaN value means "hold" (no rebalance).
    """
    cfg = resolve_config(config)
    validate_bars(bars)
    target = pd.Series(target_position)
    if not target.index.equals(bars.index):
        target = target.reindex(bars.index)

    net_eq, trades = _simulate(bars, target, cfg, apply_costs=True)
    gross_eq, _ = _simulate(bars, target, cfg, apply_costs=False)

    from .stats import summarize
    return BacktestResult(net_equity=net_eq, gross_equity=gross_eq, trades=trades,
                          stats=summarize(net_eq, trades, gross_equity=gross_eq))


__all__ = ["validate_bars", "BacktestResult", "run_backtest"]
