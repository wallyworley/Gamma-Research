"""Evaluation harness (M5): score any rule against baselines under a cost grid.

Combines the backtester, baselines, and regime attribution into a reproducible
scorecard. Needs the data stack.
"""

from .baselines import permutation_control, random_entry_control
from .harness import cost_sweep, regime_attribution, scorecard
from .volatility import (
    har_features,
    next_day_abs_return,
    next_day_range,
    realized_vol_cc,
    realized_vol_parkinson,
    vol_forecast_scorecard,
)

__all__ = [
    "random_entry_control", "permutation_control", "regime_attribution",
    "cost_sweep", "scorecard",
    # volatility-forecast harness (item 3 / F4)
    "realized_vol_cc", "realized_vol_parkinson", "next_day_abs_return",
    "next_day_range", "har_features", "vol_forecast_scorecard",
]
