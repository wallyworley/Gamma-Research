"""Fail-closed boundaries for prospective research holdouts.

Development scorers must consume physically frozen bar files whose last session is
strictly before the prospective holdout.  Merely filtering a current bar file after
loading it is not sufficient: that would still expose the holdout outcomes to the
research process.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .registry import load_and_verify_manifest


@dataclass(frozen=True)
class HoldoutPolicy:
    experiment_id: str
    parent_experiment: str
    start: pd.Timestamp
    minimum_sessions: int
    earliest_evaluation_date: pd.Timestamp
    maximum_terminal_looks: int


def load_holdout_policy(path: str | Path) -> HoldoutPolicy:
    payload = load_and_verify_manifest(path)
    rule = payload["validation"]
    return HoldoutPolicy(
        experiment_id=str(payload["experiment_id"]),
        parent_experiment=str(payload["parent_experiment"]),
        start=pd.Timestamp(rule["prospective_holdout_start"]),
        minimum_sessions=int(rule["minimum_scored_sessions"]),
        earliest_evaluation_date=pd.Timestamp(rule["earliest_evaluation_date"]),
        maximum_terminal_looks=int(rule["maximum_terminal_looks"]),
    )


def assert_frozen_development_bars(bars: pd.DataFrame, policy: HoldoutPolicy, *,
                                   source: str | Path) -> None:
    """Reject a bar input that contains even one holdout session."""
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("development bars must use a DatetimeIndex")
    if bars.index.empty:
        raise ValueError("development bars are empty")
    exposed = bars.index[bars.index >= policy.start]
    if len(exposed):
        raise ValueError(
            f"sealed holdout violation: {source} contains {len(exposed)} session(s) "
            f"on/after {policy.start.date()}; provide a physically frozen development file"
        )


def development_access_record(policy: HoldoutPolicy, *, bars_source: str,
                              first_session: pd.Timestamp,
                              last_session: pd.Timestamp, rows: int) -> dict:
    return {
        "mode": "development_only",
        "policy_id": policy.experiment_id,
        "holdout_start": str(policy.start.date()),
        "bars_source": bars_source,
        "first_outcome_session_loaded": str(first_session.date()),
        "last_outcome_session_loaded": str(last_session.date()),
        "outcome_rows_loaded": int(rows),
        "holdout_outcomes_loaded": 0,
    }


__all__ = [
    "HoldoutPolicy", "load_holdout_policy", "assert_frozen_development_bars",
    "development_access_record",
]
