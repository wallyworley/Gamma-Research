"""GEX Ratio (proxy) and a trailing-percentile helper.

Terms doc "GEX Ratio": a balance-of-power gauge; "the edge is in the trend and
where the current ratio sits within its historical range." The exact formula is
proprietary; our fixed, documented baseline is

    GEX Ratio ~= |aggregate Call GEX| / |aggregate Put GEX|

and its trailing historical percentile. The ratio is a single-snapshot scalar;
the percentile needs a time series, so it is a separate helper (fed by the
backtester in M4/M5).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import EngineConfig
from ._common import require_single_snapshot, resolve_config
from .gex import contract_gex


def gex_ratio(df: pd.DataFrame, *, config: EngineConfig | None = None) -> float:
    """|aggregate Call GEX| / |aggregate Put GEX| for one snapshot (proxy).

    Returns +inf if there is call GEX but no put GEX, and NaN if both are zero.
    """
    require_single_snapshot(df)
    cfg = resolve_config(config)
    if df.empty:
        return float("nan")
    gex = contract_gex(df, config=cfg)
    is_call = (df["type"] == "call").to_numpy()
    call_mag = abs(float(gex[is_call].sum()))
    put_mag = abs(float(gex[~is_call].sum()))
    if put_mag == 0.0:
        return float("inf") if call_mag > 0.0 else float("nan")
    return call_mag / put_mag


def trailing_percentile(series: pd.Series, value: float | None = None) -> float:
    """Percentile rank (0..1) of ``value`` (default: the last point) within ``series``.

    Fraction of observations <= value. Callers pass the trailing window they want
    (e.g. ``series.tail(window)``). Returns NaN on an empty series.
    """
    arr = np.asarray(series.dropna(), dtype=float)
    if arr.size == 0:
        return float("nan")
    if value is None:
        value = float(arr[-1])
    return float(np.mean(arr <= value))


__all__ = ["gex_ratio", "trailing_percentile"]
