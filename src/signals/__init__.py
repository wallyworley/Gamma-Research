"""Mechanical signal rules that turn chain snapshots into target weights.

Signal-layer output feeds src/backtest. Needs the data stack.
"""

from .rules import (
    chain_metric_series,
    flip_distance_series,
    flip_distance_signal,
    percentile_gate,
    regime_series,
    regime_signal,
    trend_interaction_signal,
)

__all__ = [
    "chain_metric_series",
    "regime_series",
    "regime_signal",
    "flip_distance_series",
    "flip_distance_signal",
    "percentile_gate",
    "trend_interaction_signal",
]
