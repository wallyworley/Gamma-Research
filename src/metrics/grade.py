"""grade_proxy - a transparent, owned market-structure score (NOT "Grade 11").

The terms doc is explicit: GammaEdge's "structural grade" scale, inputs, and
weights are unpublished, so we must not reverse-engineer a fake formula. Instead
we define and own a reproducible composite from the five inputs the doc lists,
and label every output a proxy.

Components (each normalized to [0, 1], higher = more positive / stable structure,
our documented convention):
  1. regime        - Net GEX relative to total chain gamma (+GEX -> higher).
  2. gex_ratio_pct - where today's GEX Ratio sits in its trailing range (supplied
                     by the caller from history; neutral 0.5 if absent).
  3. delta_skew    - above-vs-below dealer delta balance imbalance.
  4. dist_zerogex  - how far spot sits above the gamma flip.
  5. oi_proximity  - closeness of spot to the nearest key OI level (pin).

Score = 10 * sum(weight_i * component_i), weights normalized to sum to 1, so the
output is always in [0, 10]. Weights are published defaults, overridable per call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from ..config import EngineConfig
from ._common import resolve_config
from .dex import dealer_delta_balance
from .gex import contract_gex, zero_gex
from .levels import oi_levels

# Published default weights (sum to 1.0).
DEFAULT_WEIGHTS = {
    "regime": 0.30,
    "gex_ratio_pct": 0.20,
    "delta_skew": 0.20,
    "dist_zerogex": 0.20,
    "oi_proximity": 0.10,
}

_ZG_TANH_SLOPE = 5.0    # sensitivity of dist_zerogex to (spot - flip)/spot
_PROX_BAND = 0.10       # OI level within this fraction of spot scores near 1


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


@dataclass(frozen=True)
class GradeProxy:
    """Owned composite structural score in [0, 10] plus its component breakdown."""

    score_proxy: float
    components: dict


def grade_proxy(df: pd.DataFrame, *, config: EngineConfig | None = None,
                gex_ratio_percentile: float | None = None,
                weights: dict | None = None) -> GradeProxy:
    """Compute the GammaEdge-inspired proxy grade for one chain snapshot."""
    cfg = resolve_config(config)
    if df.empty:
        return GradeProxy(score_proxy=float("nan"), components={})

    spot = float(df["underlying_price"].iloc[0])
    gex = contract_gex(df, config=cfg)
    net = float(gex.sum())
    ref = float(gex.abs().sum())

    regime_score = 0.5 if ref == 0.0 else _clamp01(0.5 + 0.5 * (net / ref))
    ratio_score = 0.5 if gex_ratio_percentile is None else _clamp01(gex_ratio_percentile)
    skew_score = _clamp01(0.5 + 0.5 * dealer_delta_balance(df, config=cfg).skew_proxy)

    flip = zero_gex(df, config=cfg)
    if flip is None or spot == 0.0:
        dist_score = 0.5
    else:
        dist_score = _clamp01(0.5 + 0.5 * math.tanh(_ZG_TANH_SLOPE * (spot - flip) / spot))

    lv = oi_levels(df)
    levels = [x for x in (lv.coi_level, lv.poi_level) if x is not None]
    if not levels or spot == 0.0:
        prox_score = 0.5
    else:
        nearest = min(abs(spot - x) for x in levels) / spot
        prox_score = _clamp01(1.0 - min(nearest / _PROX_BAND, 1.0))

    components = {
        "regime": regime_score,
        "gex_ratio_pct": ratio_score,
        "delta_skew": skew_score,
        "dist_zerogex": dist_score,
        "oi_proximity": prox_score,
    }
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_w = sum(w[k] for k in components)
    score = 10.0 * sum(w[k] * components[k] for k in components) / total_w
    return GradeProxy(score_proxy=score, components=components)


__all__ = ["GradeProxy", "grade_proxy", "DEFAULT_WEIGHTS"]
