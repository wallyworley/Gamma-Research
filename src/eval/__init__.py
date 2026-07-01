"""Evaluation harness (M5): score any rule against baselines under a cost grid.

Combines the backtester, baselines, and regime attribution into a reproducible
scorecard. Needs the data stack.
"""

from .baselines import random_entry_control
from .harness import cost_sweep, regime_attribution, scorecard

__all__ = ["random_entry_control", "regime_attribution", "cost_sweep", "scorecard"]
