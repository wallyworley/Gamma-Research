"""DEX - dealer delta balance (proxy) and db_change.

Terms doc "Dealer delta balance": the DEX analog of GEX, the net modeled dealer
delta the Street must hedge, reported split above vs below spot to mirror
GammaEdge's "above/below current price" framing.

    DEX_contract = DealerSign * Delta * OpenInterest * 100 * Spot

Proxy status: the concept (DEX) is known but GammaEdge's exact formula is
proprietary; this is our transparent, config-signed baseline. Dealer sign is the
same convention used by GEX (dealers long calls / short puts), applied to the
option's signed delta.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config import EngineConfig
from ._common import CONTRACT_SIZE, dealer_signs, resolve_config


def contract_dex(df: pd.DataFrame, *, config: EngineConfig | None = None,
                 spot=None) -> pd.Series:
    """Signed per-contract DEX, index-aligned to ``df``. Uses vendor delta."""
    cfg = resolve_config(config)
    signs = dealer_signs(df, cfg.metrics.dealer_sign_convention)
    delta = df["delta"].astype("float64").fillna(0.0).to_numpy()
    oi = df["open_interest"].astype("float64").fillna(0.0).to_numpy()
    if spot is None:
        spot = df["underlying_price"].astype("float64").to_numpy()
    values = signs * delta * oi * CONTRACT_SIZE * spot
    return pd.Series(values, index=df.index, name="dex")


@dataclass(frozen=True)
class DexBalance:
    """Dealer delta balance split by strike position relative to spot (proxy)."""

    above_proxy: float   # sum of DEX for strikes above spot
    below_proxy: float   # sum of DEX for strikes below spot
    at_proxy: float      # sum of DEX for strikes exactly at spot
    net_proxy: float     # total DEX across the chain

    @property
    def skew_proxy(self) -> float:
        """Normalized above-vs-below imbalance in [-1, 1] (0 if both empty)."""
        denom = abs(self.above_proxy) + abs(self.below_proxy)
        return 0.0 if denom == 0 else (self.above_proxy - self.below_proxy) / denom


def dealer_delta_balance(df: pd.DataFrame, *, config: EngineConfig | None = None) -> DexBalance:
    """Aggregate DEX split into above-spot / below-spot / at-spot buckets."""
    if df.empty:
        return DexBalance(0.0, 0.0, 0.0, 0.0)
    dex = contract_dex(df, config=config)
    spot = float(df["underlying_price"].iloc[0])
    strike = df["strike"].astype("float64").to_numpy()
    above = float(dex[strike > spot].sum())
    below = float(dex[strike < spot].sum())
    at = float(dex[strike == spot].sum())
    return DexBalance(above_proxy=above, below_proxy=below, at_proxy=at,
                      net_proxy=above + below + at)


def db_change(balance_series: pd.Series) -> pd.Series:
    """db_change: first difference of a dealer delta-balance time series (proxy).

    Takes a series of net DEX values indexed by time (one per snapshot) and
    returns the bar-over-bar change (terms doc db_change). Needs history, so it
    lives at the series level, not the single-snapshot level.
    """
    return balance_series.diff()


__all__ = ["contract_dex", "DexBalance", "dealer_delta_balance", "db_change"]
