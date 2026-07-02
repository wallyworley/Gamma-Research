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
    Seeded (numpy default_rng) so a scorecard is fully reproducible. NOTE this
    control is LONG-ONLY; for a sign-safe timing test use permutation_control.
    """
    rng = np.random.default_rng(seed)
    draws = rng.random(len(bars))
    weights = np.where(draws < prob, weight, 0.0)
    return pd.Series(weights, index=bars.index)


def permutation_control(target_position, bars: pd.DataFrame, *, seed: int) -> pd.Series:
    """Time-shuffle of the strategy's OWN target weights over ``bars``.

    A permutation preserves the exact multiset of weights - so it matches the
    strategy's exposure and sign composition (long AND short) - and destroys only
    their timing. It does NOT preserve turnover (that is ordering-dependent), so
    the scorecard's permutation test compares GROSS returns; comparing net would
    let cost/turnover asymmetry bias a low-turnover signal (F3 follow-up).
    Comparing the strategy to many permutations is a proper test of *timing skill*
    that a random long/flat control cannot give: an always-short rule permutes to
    itself and earns zero percentile, closing the sign hole in exposure-only
    matching (F3).
    """
    w = pd.Series(target_position).reindex(bars.index).ffill().fillna(0.0)
    rng = np.random.default_rng(seed)
    return pd.Series(rng.permutation(w.to_numpy()), index=bars.index)


__all__ = ["random_entry_control", "permutation_control"]
