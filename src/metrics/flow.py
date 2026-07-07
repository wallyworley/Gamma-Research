"""Flow-based GEX variants: volume-weighted GEX, normalized GEX, convention sweep.

Three metrics motivated by the 2026-07 quant review:

  * gex_volume_proxy (item 7 - the 0DTE / EOD-OI blind spot). Standard GEX weights
    dollar gamma by open interest, but OI is a start-of-day, T-1 figure that never
    captures intraday-opened 0DTE positioning - now the majority of index option
    volume. Weighting the identical dollar-gamma formula by same-day *volume* instead
    gives a flow-based companion that does see that activity. It is a proxy twice over
    (the dealer sign is unobservable, and volume is a positioning stand-in, not held
    inventory), hence the `_proxy` suffix.

        contract volume-GEX = dealer_sign * gamma * volume * 100 * dollar_factor(spot)

  * gex_normalized (item 4 - cross-sectional comparability). Raw $GEX ranks names by
    size, not by structural intensity, so it cannot be compared across a universe.
    Dividing Net GEX by a size denominator makes it comparable. The self-contained
    default is total option notional (sum of OI * 100 * spot over the chain); a caller
    may instead pass an explicit denominator (e.g. dollar ADV from bars) to choose
    market-cap or ADV normalization.

  * net_gex_by_convention (item 5 groundwork - the F11 sign-convention sweep). Returns
    Net GEX under each requested dealer-sign convention in one call, varying ONLY the
    sign convention (pricer r/q, gex form, grid all held fixed), so downstream
    evaluation can flag any conclusion that flips under the alternate convention.

Dealer sign and the GEX form come from EngineConfig, never hard-coded (terms doc
"Foundational caveat").
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from ..config import EngineConfig
from ._common import (
    CONTRACT_SIZE,
    dealer_signs,
    dollar_factor,
    require_single_snapshot,
    resolve_config,
)
from .gex import net_gex


# --------------------------------------------------------------------------- #
# 1. Volume-weighted GEX (proxy) - mirrors gex.py's API shapes on volume weight
# --------------------------------------------------------------------------- #

def contract_gex_volume_proxy(df: pd.DataFrame, *, config: EngineConfig | None = None,
                              spot=None) -> pd.Series:
    """Signed per-contract volume-weighted GEX (proxy), index-aligned to ``df``.

    Identical dollar-gamma formula to gex.contract_gex, but weighted by same-day
    ``volume`` instead of ``open_interest``. Null volume -> 0 weight (mirrors gex.py's
    handling of null open interest). ``spot`` overrides the per-row underlying_price
    in the dollar factor.
    """
    cfg = resolve_config(config)
    signs = dealer_signs(df, cfg.metrics.dealer_sign_convention)
    gamma = df["gamma"].astype("float64").fillna(0.0).to_numpy()
    volume = df["volume"].astype("float64").fillna(0.0).to_numpy()
    if spot is None:
        spot = df["underlying_price"].astype("float64").to_numpy()
    factor = dollar_factor(spot, cfg.metrics.gex_convention)
    values = signs * gamma * volume * CONTRACT_SIZE * factor
    return pd.Series(values, index=df.index, name="gex_volume_proxy")


def net_gex_volume_proxy(df: pd.DataFrame, *, config: EngineConfig | None = None,
                         spot=None) -> float:
    """Aggregate volume-weighted GEX over the chain (proxy, dealer-signed)."""
    require_single_snapshot(df)
    if df.empty:
        return 0.0
    return float(contract_gex_volume_proxy(df, config=config, spot=spot).sum())


def gex_volume_by_strike_proxy(df: pd.DataFrame, *, config: EngineConfig | None = None) -> pd.Series:
    """Volume-weighted GEX summed per strike (proxy), sorted by strike."""
    require_single_snapshot(df)
    gex = contract_gex_volume_proxy(df, config=config)
    return gex.groupby(df["strike"]).sum().sort_index()


# --------------------------------------------------------------------------- #
# 2. Normalized GEX (cross-sectional comparability)
# --------------------------------------------------------------------------- #

def option_notional(df: pd.DataFrame) -> float:
    """Total option notional = sum over the chain of open_interest * 100 * spot.

    Self-contained size denominator (no external data): null OI counts as 0.
    """
    if df.empty:
        return 0.0
    oi = df["open_interest"].astype("float64").fillna(0.0).to_numpy()
    spot = df["underlying_price"].astype("float64").to_numpy()
    return float(np.sum(oi * CONTRACT_SIZE * spot))


def gex_normalized(df: pd.DataFrame, *, denominator="option_notional",
                   config: EngineConfig | None = None) -> float | None:
    """Net GEX divided by a size denominator, for cross-sectional comparability.

    ``denominator`` is either the string ``"option_notional"`` (the self-contained
    default: sum of OI * 100 * spot over the chain) or an explicit positive float the
    caller computed (e.g. dollar ADV = ADV_shares * spot), letting the caller choose
    market-cap or ADV normalization. The Net GEX numerator uses the configured
    gex_convention, so a dollar_per_1pct numerator over a dollar notional yields a
    per-1%-move intensity. Returns None when the denominator is 0, negative, NaN, or
    non-finite (an undefined ratio), rather than an infinity.
    """
    require_single_snapshot(df)
    cfg = resolve_config(config)
    if isinstance(denominator, str):
        if denominator != "option_notional":
            raise ValueError(
                f"unknown denominator {denominator!r}; pass 'option_notional' or a float")
        denom = option_notional(df)
    else:
        denom = float(denominator)
    if not np.isfinite(denom) or denom <= 0:
        return None
    return net_gex(df, config=cfg) / denom


# --------------------------------------------------------------------------- #
# 4. Dealer-sign convention sweep (F11 groundwork)
# --------------------------------------------------------------------------- #

def net_gex_by_convention(
    df: pd.DataFrame,
    *,
    conventions=("long_call_short_put", "otm_customer"),
    config: EngineConfig | None = None,
) -> dict[str, float]:
    """Net GEX under each dealer-sign convention, {convention: net_gex}.

    Varies ONLY the dealer_sign_convention; every other pinned assumption (pricer
    r/q, gex form, grid) is held fixed so the sweep isolates sign-convention risk.
    Feeds the F11 sensitivity check: a signal whose sign flips across conventions is
    riding on the unobservable assumption, not on structure.
    """
    require_single_snapshot(df)
    cfg = resolve_config(config)
    out: dict[str, float] = {}
    for conv in conventions:
        conv_cfg = replace(cfg, metrics=replace(cfg.metrics, dealer_sign_convention=conv))
        out[conv] = net_gex(df, config=conv_cfg)
    return out


__all__ = [
    "contract_gex_volume_proxy",
    "net_gex_volume_proxy",
    "gex_volume_by_strike_proxy",
    "option_notional",
    "gex_normalized",
    "net_gex_by_convention",
]
