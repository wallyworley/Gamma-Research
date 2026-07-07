"""Gamma Exposure (GEX) metrics: Net GEX, per-strike GEX, regime, ZeroGEX.

Grounded in docs/reddit_gamma_strategy_terms.md ("The base concept: GEX" and
"ZeroGEX"). All formulas are the transparent, reproducible baselines defined
there; the dealer sign and GEX form come from EngineConfig (pinned in M0), never
hard-coded, because the dealer-position assumption is unobservable and every
number depends on it (terms doc "Foundational caveat").

Per-contract dollar GEX (dollar-per-1%-move form):

    GEX = sign(type) * gamma * open_interest * 100 * (spot^2 * 0.01)

with sign = +1 for calls, -1 for puts under the standard
`long_call_short_put` convention. Net GEX sums that over the chain; regime is its
sign; ZeroGEX is the spot where it flips (via a BS-gamma recompute, see
blackscholes.py). Open interest is used exactly as it arrives on the snapshot,
carrying whatever `oi_asof_date` the adapter stamped; **no layer shifts OI across
time** (there is no time-series T-1 realignment - a prior claim that the
backtester did this was false, review finding F1). A single snapshot is
point-in-time correct only insofar as the adapter's `oi_asof_date` assumption
holds; verify it before trusting live results.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import EngineConfig
from ._common import (
    CONTRACT_SIZE,
    dealer_signs,
    dollar_factor,
    require_single_snapshot,
    resolve_config,
    years_to_expiry,
)
from .blackscholes import bs_gamma


def contract_gex(df: pd.DataFrame, *, config: EngineConfig | None = None,
                 spot=None) -> pd.Series:
    """Signed per-contract GEX, index-aligned to ``df``. Uses vendor gamma.

    ``spot`` overrides the per-row underlying_price used in the dollar factor
    (the ZeroGEX solver passes a candidate spot; normal callers leave it None).
    """
    cfg = resolve_config(config)
    signs = dealer_signs(df, cfg.metrics.dealer_sign_convention)
    gamma = df["gamma"].astype("float64").fillna(0.0).to_numpy()
    oi = df["open_interest"].astype("float64").fillna(0.0).to_numpy()
    if spot is None:
        spot = df["underlying_price"].astype("float64").to_numpy()
    factor = dollar_factor(spot, cfg.metrics.gex_convention)
    values = signs * gamma * oi * CONTRACT_SIZE * factor
    return pd.Series(values, index=df.index, name="gex")


def net_gex(df: pd.DataFrame, *, config: EngineConfig | None = None, spot=None) -> float:
    """Aggregate signed GEX over the whole chain (dealer-signed)."""
    require_single_snapshot(df)
    if df.empty:
        return 0.0
    return float(contract_gex(df, config=config, spot=spot).sum())


def gex_by_strike(df: pd.DataFrame, *, config: EngineConfig | None = None) -> pd.Series:
    """Signed GEX summed per strike (across calls, puts, expirations), sorted."""
    require_single_snapshot(df)
    gex = contract_gex(df, config=config)
    return gex.groupby(df["strike"]).sum().sort_index()


def regime(net_value: float) -> str:
    """+GEX (dealers long gamma) / -GEX (short gamma) / flat, from Net GEX sign."""
    if net_value > 0:
        return "+GEX"
    if net_value < 0:
        return "-GEX"
    return "flat"


def _zero_gex_detail(df: pd.DataFrame, cfg: EngineConfig) -> dict:
    """Internal: ZeroGEX flip plus the grid it searched and the BS net at spot.

    Recomputes each option's gamma via Black-Scholes at candidate spots over the
    config grid, forms Net GEX(S), and interpolates the sign change nearest spot.
    Contracts without a positive T, sigma (iv), and open interest are excluded.
    """
    lo, hi, n = (cfg.metrics.zerogex_grid_lo_frac, cfg.metrics.zerogex_grid_hi_frac,
                 cfg.metrics.zerogex_grid_n)
    if df.empty:
        return {"flip": None, "grid_lo": None, "grid_hi": None, "bs_net_at_spot": None, "priced": 0}

    spot0 = float(df["underlying_price"].iloc[0])
    signs = dealer_signs(df, cfg.metrics.dealer_sign_convention)
    K = df["strike"].astype("float64").to_numpy()
    sigma = df["iv"].astype("float64").fillna(0.0).to_numpy()
    oi = df["open_interest"].astype("float64").fillna(0.0).to_numpy()
    T = years_to_expiry(df, cfg.pricer.day_count)

    keep = (T > 0) & (sigma > 0) & (oi > 0)
    if not keep.any():
        return {"flip": None, "grid_lo": spot0 * lo, "grid_hi": spot0 * hi,
                "bs_net_at_spot": None, "priced": 0}
    signs, K, sigma, oi, T = signs[keep], K[keep], sigma[keep], oi[keep], T[keep]

    r, q = cfg.pricer.risk_free_rate, cfg.pricer.dividend_yield
    is_dollar = cfg.metrics.gex_convention == "dollar_per_1pct"

    def net_at(s: float) -> float:
        gamma_s = bs_gamma(s, K, T, sigma, r, q)
        factor = (s * s * 0.01) if is_dollar else 1.0
        return float(np.sum(signs * gamma_s * oi * CONTRACT_SIZE * factor))

    grid = np.linspace(spot0 * lo, spot0 * hi, n)
    nets = np.array([net_at(s) for s in grid])

    crossings: list[float] = []
    for i in range(len(grid) - 1):
        a, b = nets[i], nets[i + 1]
        if a == 0.0:
            crossings.append(float(grid[i]))
        elif a * b < 0.0:
            crossings.append(float(grid[i] - a * (grid[i + 1] - grid[i]) / (b - a)))
    if nets[-1] == 0.0:
        crossings.append(float(grid[-1]))
    flip = min(crossings, key=lambda s: abs(s - spot0)) if crossings else None
    return {"flip": flip, "grid_lo": float(grid[0]), "grid_hi": float(grid[-1]),
            "bs_net_at_spot": net_at(spot0), "priced": int(len(K))}


def zero_gex(df: pd.DataFrame, *, config: EngineConfig | None = None) -> float | None:
    """Spot where Net GEX crosses zero (the gamma flip), via a BS-gamma recompute.

    Returns the crossing nearest spot, or None. **None means "no crossing within
    the searched grid"** (config `metrics.zerogex_grid_*`), NOT a proof that no
    flip exists - the true flip may lie outside the grid. See `gamma_snapshot`,
    which exposes `zero_gex_in_grid` and the search range.
    """
    require_single_snapshot(df)
    return _zero_gex_detail(df, resolve_config(config))["flip"]


@dataclass(frozen=True)
class GexSnapshot:
    """One point-in-time gamma-structure summary for a chain snapshot."""

    net_gex: float
    regime: str
    zero_gex: float | None
    spot: float
    zero_gex_in_grid: bool        # F10: True if a flip was found inside the searched grid
    gamma_source_agrees: bool     # F13: vendor-gamma regime and BS-gamma sign at spot agree


def gamma_snapshot(df: pd.DataFrame, *, config: EngineConfig | None = None) -> GexSnapshot:
    """Compute Net GEX, regime, and ZeroGEX for one validated chain snapshot.

    Flags two consistency facts: whether the flip was found inside the search grid
    (F10) and whether the vendor-gamma regime agrees in sign with the BS-gamma net
    at spot (F13) - a disagreement means the (regime, zero_gex) pair is internally
    inconsistent and should be treated with caution.
    """
    require_single_snapshot(df)
    cfg = resolve_config(config)
    net = net_gex(df, config=cfg)
    spot = float(df["underlying_price"].iloc[0]) if not df.empty else float("nan")
    detail = _zero_gex_detail(df, cfg)
    bs_at_spot = detail["bs_net_at_spot"]
    agrees = True if (bs_at_spot is None or net == 0.0) else ((net > 0) == (bs_at_spot > 0))
    return GexSnapshot(net_gex=net, regime=regime(net), zero_gex=detail["flip"], spot=spot,
                       zero_gex_in_grid=(detail["flip"] is not None), gamma_source_agrees=agrees)


__all__ = [
    "contract_gex",
    "net_gex",
    "gex_by_strike",
    "regime",
    "zero_gex",
    "GexSnapshot",
    "gamma_snapshot",
]
