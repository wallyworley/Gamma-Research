"""Mechanical signal rules that turn chain snapshots into target weights.

Signal-layer output feeds src/backtest. Needs the data stack.
"""

from .rules import chain_metric_series, regime_series, regime_signal

__all__ = ["chain_metric_series", "regime_series", "regime_signal"]
