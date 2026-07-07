"""Dealer-signed vanna and charm exposures (proxies).

Quant review item 6: second-order flows - delta's sensitivity to implied vol
(vanna) and to the passage of time (charm) - drive the documented OpEx/expiration
hedging effects. The vendor snapshot carries delta/vega/theta but not vanna/charm,
so we recompute them per contract from the stored iv/strike/expiration/underlying
via Black-Scholes (blackscholes.bs_vanna / bs_charm), using the pinned PricerConfig
r/q/day-count so every greek shares one basis.

Exposures mirror the DEX shape (dex.py): a modeled dealer-hedge dollar figure under
the unobservable dealer-sign convention, so both carry the `_proxy` suffix.

    net_vanna_proxy = sum over chain of
        dealer_sign * (vanna * VOL_POINT) * open_interest * 100 * spot
      = dollars of delta-hedge flow the Street must trade per +1 IV vol-point (0.01).

    net_charm_proxy = sum over chain of
        dealer_sign * (charm / days_per_year) * open_interest * 100 * spot
      = dollars of delta-hedge flow per +1 calendar day of time passing.

Units note. bs_vanna is dDelta per 1.00 change in sigma, so VOL_POINT = 0.01
converts it to a per-vol-point figure; bs_charm is delta per YEAR, so dividing by
the day-count's days_per_year (365 for act/365) converts it to per calendar day.
Multiplying a per-share delta change by open_interest * 100 (shares) * spot ($/share)
gives a dollar delta-hedge notional, exactly as DEX does for the level of delta.

Null / non-positive IV rows cannot be priced and are skipped; the returned
dataclass exposes how many were skipped vs priced (a low priced-fraction means the
aggregate only reflects part of the book - the same coverage caveat as greek_coverage).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import EngineConfig
from ._common import (
    CONTRACT_SIZE,
    DAY_COUNTS,
    dealer_signs,
    require_single_snapshot,
    resolve_config,
    years_to_expiry,
)
from .blackscholes import bs_charm, bs_vanna

# One implied-vol point = 0.01 (a 1% absolute move in IV). net_vanna_proxy is
# reported per vol-point, so raw dDelta/dsigma is scaled by this.
VOL_POINT = 0.01


@dataclass(frozen=True)
class VannaExposure:
    """Dealer vanna exposure for one snapshot (proxy)."""

    net_vanna_proxy: float   # $ of delta-hedge flow per +1 vol-point (0.01) IV rise
    n_priced: int            # rows with usable IV that were priced
    n_skipped: int           # rows skipped for missing / non-positive IV


@dataclass(frozen=True)
class CharmExposure:
    """Dealer charm exposure for one snapshot (proxy)."""

    net_charm_proxy: float   # $ of delta-hedge flow per +1 calendar day passing
    n_priced: int            # rows with usable IV that were priced
    n_skipped: int           # rows skipped for missing / non-positive IV


def _priced_inputs(df: pd.DataFrame, cfg: EngineConfig):
    """Shared arrays for the two exposures: signs, S, K, T, sigma, oi, and the
    usable-IV mask (missing or non-positive IV cannot be priced and is skipped)."""
    signs = dealer_signs(df, cfg.metrics.dealer_sign_convention)
    iv = df["iv"].astype("float64").to_numpy()
    usable = np.isfinite(iv) & (iv > 0)
    spot = df["underlying_price"].astype("float64").to_numpy()
    strike = df["strike"].astype("float64").to_numpy()
    oi = df["open_interest"].astype("float64").fillna(0.0).to_numpy()
    tau = years_to_expiry(df, cfg.pricer.day_count)
    return signs, spot, strike, tau, iv, oi, usable


def net_vanna_exposure(df: pd.DataFrame, *, config: EngineConfig | None = None) -> VannaExposure:
    """Aggregate dealer vanna exposure (proxy), skipping null/non-positive-IV rows.

    Positive net_vanna_proxy => a rise in IV pushes the Street's aggregate hedge
    that many dollars of delta long (buy), a fall that many short.
    """
    require_single_snapshot(df)
    cfg = resolve_config(config)
    if df.empty:
        return VannaExposure(0.0, 0, 0)
    signs, spot, strike, tau, iv, oi, usable = _priced_inputs(df, cfg)
    r, q = cfg.pricer.risk_free_rate, cfg.pricer.dividend_yield
    vanna = bs_vanna(spot, strike, tau, iv, r, q)  # 0.0 where inputs degenerate
    per_contract = signs * (vanna * VOL_POINT) * oi * CONTRACT_SIZE * spot
    net = float(np.sum(np.where(usable, per_contract, 0.0)))
    return VannaExposure(net_vanna_proxy=net, n_priced=int(usable.sum()),
                         n_skipped=int((~usable).sum()))


def net_charm_exposure(df: pd.DataFrame, *, config: EngineConfig | None = None) -> CharmExposure:
    """Aggregate dealer charm exposure (proxy), skipping null/non-positive-IV rows.

    Positive net_charm_proxy => one calendar day passing pushes the Street's
    aggregate hedge that many dollars of delta long (buy), holding spot/IV fixed.
    """
    require_single_snapshot(df)
    cfg = resolve_config(config)
    if df.empty:
        return CharmExposure(0.0, 0, 0)
    signs, spot, strike, tau, iv, oi, usable = _priced_inputs(df, cfg)
    r, q = cfg.pricer.risk_free_rate, cfg.pricer.dividend_yield
    is_call = (df["type"] == "call").to_numpy()
    days_per_year = DAY_COUNTS[cfg.pricer.day_count]
    charm = bs_charm(spot, strike, tau, iv, r, q, is_call)  # delta per year
    per_contract = signs * (charm / days_per_year) * oi * CONTRACT_SIZE * spot
    net = float(np.sum(np.where(usable, per_contract, 0.0)))
    return CharmExposure(net_charm_proxy=net, n_priced=int(usable.sum()),
                         n_skipped=int((~usable).sum()))


__all__ = [
    "VOL_POINT",
    "VannaExposure",
    "CharmExposure",
    "net_vanna_exposure",
    "net_charm_exposure",
]
