"""Event-driven, non-live backtester on the underlying (M4).

Simulated fills only - no broker, no orders. run_backtest consumes a bar
timeline plus a per-bar target-weight series and returns net/gross equity, a
trade log, and summary stats. Needs the data stack.
"""

from .engine import BacktestResult, run_backtest, validate_bars
from .stats import buy_and_hold, max_drawdown, summarize, total_return

__all__ = [
    "run_backtest",
    "BacktestResult",
    "validate_bars",
    "total_return",
    "max_drawdown",
    "buy_and_hold",
    "summarize",
]
