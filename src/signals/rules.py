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

import math
from typing import Callable

import pandas as pd

from ..config import EngineConfig
from ..metrics.gex import gamma_snapshot, net_gex, regime
from ..metrics.ratios import trailing_percentile


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


# --------------------------------------------------------------------------- #
# Signal depth (quant review item 14): the thin regime-sign rule is only the
# start. Distance-to-flip is a continuous, signed signal (more informative than
# sign); a percentile gate encodes "the middle of the range has no edge"; and
# trend-interaction encodes the practitioner conditional (follow in -GEX, fade in
# +GEX). Every rule below uses only information available at the decision date t;
# the weight decided from bar t fills at t+1's open (the engine enforces it).
# --------------------------------------------------------------------------- #

def flip_distance_series(chains, *, config: EngineConfig | None = None) -> pd.Series:
    """Per-date signed distance from spot to the gamma flip: (spot - ZeroGEX) / spot.

    Continuous and signed: positive when spot sits ABOVE the flip (in +gamma
    territory, where dealer hedging is stabilizing) and negative below it. NaN when
    ZeroGEX is None - i.e. Net GEX does not cross zero anywhere in the searched grid
    (`metrics.zerogex_grid_*`), so there is no flip level to measure a distance to.
    A continuous distance is likely more informative than the raw +/-/flat regime
    sign (item 14). Indexed by bar timestamp.
    """
    def _dist(df: pd.DataFrame) -> float:
        snap = gamma_snapshot(df, config=config)
        if snap.zero_gex is None or not math.isfinite(snap.spot) or snap.spot == 0:
            return float("nan")
        return (snap.spot - snap.zero_gex) / snap.spot

    return chain_metric_series(chains, _dist)


def flip_distance_signal(chains, *, threshold: float, long: float = 1.0,
                         short: float = -1.0, flat: float = 0.0,
                         config: EngineConfig | None = None) -> pd.Series:
    """Target weights from distance-to-flip: long safely above the flip, short below.

    ``threshold`` is a FRACTION of spot (e.g. 0.005 = 0.5%): go ``long`` when the
    distance exceeds +threshold (spot comfortably above the flip, in +gamma /
    vol-suppressed territory), ``short`` when below -threshold (spot under the flip,
    in -gamma / vol-amplified territory), else ``flat``. A NaN distance (no flip in
    the grid) maps to ``flat`` - with no flip level there is no directional call.
    The weight for bar t is decided from bar t's chain and filled at t+1's open.
    """
    dist = flip_distance_series(chains, config=config)

    def _map(x: float) -> float:
        if not math.isfinite(x):
            return flat
        if x > threshold:
            return long
        if x < -threshold:
            return short
        return flat

    return dist.map(_map)


def percentile_gate(signal: pd.Series, series: pd.Series, *, window: int,
                    low: float, high: float) -> pd.Series:
    """Gate a target-weight series to flat when a metric sits mid-range (no edge).

    Composable design (preferred over a bespoke regime_percentile_signal): it gates
    ANY existing weight series, decoupling the gate from how the weights were built.
    For each date t the metric's trailing percentile is `trailing_percentile` of
    ``series`` over its trailing ``window`` (the fraction of the window <= the value
    at t); when that percentile is INSIDE [low, high] the weight is forced to 0.0,
    otherwise the original weight passes through. This encodes "the middle of the
    range has no edge" (terms doc): only extreme readings keep a position.

    Point-in-time honest: the percentile at t uses ``series`` values through t only.
    A NaN percentile (no data yet) leaves the weight unchanged. ``signal`` and
    ``series`` are aligned on their shared index.
    """
    series = series.astype("float64")
    out = {}
    for t in signal.index:
        w = signal.loc[t]
        hist = series.loc[:t]
        pct = trailing_percentile(hist.tail(window)) if hist.notna().any() else float("nan")
        gated = math.isfinite(pct) and low <= pct <= high
        out[t] = 0.0 if gated else w
    return pd.Series(out, index=signal.index)


def trend_interaction_signal(chains, bars: pd.DataFrame, *, lookback: int = 5,
                             config: EngineConfig | None = None) -> pd.Series:
    """The practitioner conditional: follow trend in -GEX, fade it in +GEX.

    In a -GEX (dealer short gamma) regime dealer hedging AMPLIFIES moves, so the
    trailing ``lookback``-day return tends to continue -> FOLLOW its sign. In a +GEX
    regime hedging SUPPRESSES / mean-reverts moves -> FADE its sign. Flat when the
    regime is flat or the trailing return is exactly zero. Trend-interaction is a
    *conditional* rule (item 14), not a standalone signal.

    No lookahead: the regime comes from bar t's chain and the trailing return uses
    closes through t (C_t / C_{t-lookback} - 1), both known at the close of t; the
    resulting weight is filled at t+1's open by the engine. Dates with fewer than
    ``lookback`` prior bars are flat (insufficient history). Indexed by bar timestamp.
    """
    close = bars["close"].astype("float64")
    positions = {ts: i for i, ts in enumerate(close.index)}   # avoids get_loc return-type quirks

    def _trail_ret(ts) -> float:
        pos = positions.get(ts)
        if pos is None or pos < lookback:
            return float("nan")
        return float(close.iloc[pos] / close.iloc[pos - lookback] - 1.0)

    def _weight(ts, df: pd.DataFrame) -> float:
        reg = regime(net_gex(df, config=config))
        r = _trail_ret(ts)
        if reg == "flat" or not math.isfinite(r) or r == 0.0:
            return 0.0
        trend = 1.0 if r > 0 else -1.0
        return trend if reg == "-GEX" else -trend       # follow in -GEX, fade in +GEX

    return pd.Series({ts: _weight(ts, df) for ts, df in _iter_chains(chains)}).sort_index()


__all__ = [
    "chain_metric_series",
    "regime_series",
    "regime_signal",
    "flip_distance_series",
    "flip_distance_signal",
    "percentile_gate",
    "trend_interaction_signal",
]
