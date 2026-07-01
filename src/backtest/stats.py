"""Performance statistics and a buy-and-hold baseline (M4).

Deliberately small and honest: total return, max drawdown, trade count, and the
cost drag between gross and net. Richer scoring (regime attribution, random-entry
control, cost-grid sweeps) is the M5 evaluation harness.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def total_return(equity: pd.Series) -> float:
    """Cumulative return of an equity curve (last/first - 1)."""
    if equity.empty:
        return float("nan")
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def max_drawdown(equity: pd.Series) -> float:
    """Worst peak-to-trough decline as a negative fraction (0.0 if never down)."""
    if equity.empty:
        return float("nan")
    running_peak = equity.cummax()
    return float((equity / running_peak - 1.0).min())


def buy_and_hold(bars: pd.DataFrame, initial_capital: float) -> pd.Series:
    """Equity curve from holding the underlying at each bar's close."""
    close = bars["close"].astype("float64")
    return initial_capital * close / float(close.iloc[0])


def summarize(net_equity: pd.Series, trades: pd.DataFrame, *,
              gross_equity: pd.Series | None = None) -> dict:
    """Headline stats for a run: returns, drawdown, trades, and cost drag."""
    stats = {
        "initial_equity": float(net_equity.iloc[0]) if not net_equity.empty else float("nan"),
        "final_equity": float(net_equity.iloc[-1]) if not net_equity.empty else float("nan"),
        "total_return": total_return(net_equity),
        "max_drawdown": max_drawdown(net_equity),
        "n_trades": int(len(trades)),
        "total_cost": float(trades["cost"].sum()) if not trades.empty else 0.0,
    }
    if gross_equity is not None and not gross_equity.empty:
        stats["gross_total_return"] = total_return(gross_equity)
        stats["cost_drag"] = stats["gross_total_return"] - stats["total_return"]
    return stats


__all__ = ["total_return", "max_drawdown", "buy_and_hold", "summarize"]
