"""Mechanical signal rules: chain snapshots -> per-bar target weights.

A signal turns a time series of point-in-time chain snapshots into the
target-weight series the backtester consumes. The engine already enforces the
no-lookahead fill: the weight decided from bar t's chain executes at t+1's open
(src/backtest/engine.py), so signal code just needs to key each weight by the
bar timestamp it was decided on.

``chains`` is an ordered mapping ``{bar_timestamp: chain_df}`` (or a list of
``(bar_timestamp, chain_df)``), keyed to match ``bars.index`` in the backtester.
Rules stay transparent and config-driven (dealer sign etc. from EngineConfig);
GammaEdge's real rule weights are unpublished, so these are owned baselines.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from ..config import EngineConfig
from ..metrics.gex import net_gex, regime


def _iter_chains(chains):
    items = list(chains.items()) if isinstance(chains, dict) else list(chains)
    return sorted(items, key=lambda kv: kv[0])


def chain_metric_series(chains, fn: Callable[[pd.DataFrame], float]) -> pd.Series:
    """Apply ``fn(chain_df)`` to each snapshot -> a series indexed by bar timestamp.

    The generic building block: use it to turn any per-snapshot metric (Net GEX,
    GEX Ratio, grade, ...) into a time series for signals or for the harness's
    history-dependent inputs.
    """
    return pd.Series({ts: fn(df) for ts, df in _iter_chains(chains)}).sort_index()


def regime_series(chains, *, config: EngineConfig | None = None) -> pd.Series:
    """Per-snapshot +GEX / -GEX / flat label, indexed by bar timestamp."""
    return chain_metric_series(chains, lambda df: regime(net_gex(df, config=config)))


def regime_signal(chains, *, config: EngineConfig | None = None,
                  long: float = 1.0, flat: float = 0.0, short: float = 0.0) -> pd.Series:
    """Target weights from the GEX regime: +GEX -> long, -GEX -> short, flat -> flat.

    Default short=0.0 makes this a long/flat rule. The weight for bar t is decided
    from bar t's chain and (via the engine) filled at t+1's open.
    """
    mapping = {"+GEX": long, "-GEX": short, "flat": flat}
    return chain_metric_series(chains, lambda df: mapping[regime(net_gex(df, config=config))])


__all__ = ["chain_metric_series", "regime_series", "regime_signal"]
