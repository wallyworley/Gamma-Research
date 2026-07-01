"""Baseline controls for the evaluation harness.

A rule only earns credit if it beats dumb comparators under the SAME cost model
(docs/phase_1_plan.md section 8 "Baseline comparison"). Buy-and-hold lives in
backtest/stats.py; here is the random-entry control - random timing, so any edge
in the real rule has to come from timing, not from being in the market.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def random_entry_control(bars: pd.DataFrame, *, seed: int, weight: float = 1.0,
                         prob: float = 0.5) -> pd.Series:
    """A reproducible random long/flat target-weight series over ``bars``.

    Each bar independently targets ``weight`` with probability ``prob`` else 0.
    Seeded (numpy default_rng) so a scorecard is fully reproducible.
    """
    rng = np.random.default_rng(seed)
    draws = rng.random(len(bars))
    weights = np.where(draws < prob, weight, 0.0)
    return pd.Series(weights, index=bars.index)


__all__ = ["random_entry_control"]
