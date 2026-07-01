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
blackscholes.py). Open interest is used as reported; point-in-time T-1 alignment
of OI is the backtester's job (M4), not this layer's.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import EngineConfig
from .blackscholes import bs_gamma

_CONTRACT_SIZE = 100

_DEALER_SIGNS = {
    "long_call_short_put": {"call": 1.0, "put": -1.0},
    "short_call_long_put": {"call": -1.0, "put": 1.0},
}

_DAY_COUNTS = {"act/365": 365.0, "act/365.25": 365.25}


def _cfg(config: EngineConfig | None) -> EngineConfig:
    return config if config is not None else EngineConfig.default()


def _signs(df: pd.DataFrame, convention: str) -> np.ndarray:
    try:
        mapping = _DEALER_SIGNS[convention]
    except KeyError:
        raise ValueError(
            f"unknown dealer_sign_convention {convention!r}; "
            f"known: {sorted(_DEALER_SIGNS)}") from None
    return df["type"].map(mapping).fillna(0.0).to_numpy(dtype=float)


def _dollar_factor(spot, gex_convention: str):
    """Scalar (or array) weight applied on top of shares. Spot may be array."""
    if gex_convention == "dollar_per_1pct":
        return np.asarray(spot, dtype=float) ** 2 * 0.01
    if gex_convention == "shares":
        return 1.0
    raise ValueError(f"unknown gex_convention {gex_convention!r}; known: dollar_per_1pct, shares")


def contract_gex(df: pd.DataFrame, *, config: EngineConfig | None = None,
                 spot=None) -> pd.Series:
    """Signed per-contract GEX, index-aligned to ``df``. Uses vendor gamma.

    ``spot`` overrides the per-row underlying_price used in the dollar factor
    (the ZeroGEX solver passes a candidate spot; normal callers leave it None).
    """
    cfg = _cfg(config)
    signs = _signs(df, cfg.metrics.dealer_sign_convention)
    gamma = df["gamma"].astype("float64").fillna(0.0).to_numpy()
    oi = df["open_interest"].astype("float64").fillna(0.0).to_numpy()
    if spot is None:
        spot = df["underlying_price"].astype("float64").to_numpy()
    factor = _dollar_factor(spot, cfg.metrics.gex_convention)
    values = signs * gamma * oi * _CONTRACT_SIZE * factor
    return pd.Series(values, index=df.index, name="gex")


def net_gex(df: pd.DataFrame, *, config: EngineConfig | None = None, spot=None) -> float:
    """Aggregate signed GEX over the whole chain (dealer-signed)."""
    if df.empty:
        return 0.0
    return float(contract_gex(df, config=config, spot=spot).sum())


def gex_by_strike(df: pd.DataFrame, *, config: EngineConfig | None = None) -> pd.Series:
    """Signed GEX summed per strike (across calls, puts, expirations), sorted."""
    gex = contract_gex(df, config=config)
    return gex.groupby(df["strike"]).sum().sort_index()


def regime(net_value: float) -> str:
    """+GEX (dealers long gamma) / -GEX (short gamma) / flat, from Net GEX sign."""
    if net_value > 0:
        return "+GEX"
    if net_value < 0:
        return "-GEX"
    return "flat"


def _years_to_expiry(df: pd.DataFrame, day_count: str) -> np.ndarray:
    try:
        denom = _DAY_COUNTS[day_count]
    except KeyError:
        raise NotImplementedError(
            f"day_count {day_count!r} not supported; known: {sorted(_DAY_COUNTS)}") from None
    qd = df["quote_ts"].dt.tz_convert("UTC").dt.date
    ed = df["expiration"].dt.date
    days = np.array([(e - d).days for e, d in zip(ed, qd)], dtype=float)
    return days / denom


def zero_gex(df: pd.DataFrame, *, config: EngineConfig | None = None,
             lo_frac: float = 0.7, hi_frac: float = 1.3, n: int = 121) -> float | None:
    """Spot where Net GEX crosses zero (the gamma flip), or None if no crossing.

    Recomputes each option's gamma via Black-Scholes at candidate spots over a
    grid around the current spot, forms Net GEX(S), and linearly interpolates the
    sign change nearest to spot. Contracts without a positive T, sigma (iv), and
    open interest cannot be repriced and are excluded.
    """
    cfg = _cfg(config)
    if df.empty:
        return None

    spot0 = float(df["underlying_price"].iloc[0])
    signs = _signs(df, cfg.metrics.dealer_sign_convention)
    K = df["strike"].astype("float64").to_numpy()
    sigma = df["iv"].astype("float64").fillna(0.0).to_numpy()
    oi = df["open_interest"].astype("float64").fillna(0.0).to_numpy()
    T = _years_to_expiry(df, cfg.pricer.day_count)

    keep = (T > 0) & (sigma > 0) & (oi > 0)
    if not keep.any():
        return None
    signs, K, sigma, oi, T = signs[keep], K[keep], sigma[keep], oi[keep], T[keep]

    r, q = cfg.pricer.risk_free_rate, cfg.pricer.dividend_yield
    is_dollar = cfg.metrics.gex_convention == "dollar_per_1pct"

    def net_at(s: float) -> float:
        gamma_s = bs_gamma(s, K, T, sigma, r, q)
        factor = (s * s * 0.01) if is_dollar else 1.0
        return float(np.sum(signs * gamma_s * oi * _CONTRACT_SIZE * factor))

    grid = np.linspace(spot0 * lo_frac, spot0 * hi_frac, n)
    nets = np.array([net_at(s) for s in grid])

    # Linear-interpolate every sign change; return the crossing nearest spot0.
    crossings: list[float] = []
    for i in range(len(grid) - 1):
        a, b = nets[i], nets[i + 1]
        if a == 0.0:
            crossings.append(float(grid[i]))
        elif a * b < 0.0:
            crossings.append(float(grid[i] - a * (grid[i + 1] - grid[i]) / (b - a)))
    if nets[-1] == 0.0:
        crossings.append(float(grid[-1]))
    if not crossings:
        return None
    return min(crossings, key=lambda s: abs(s - spot0))


@dataclass(frozen=True)
class GexSnapshot:
    """One point-in-time gamma-structure summary for a chain snapshot."""

    net_gex: float
    regime: str
    zero_gex: float | None
    spot: float


def gamma_snapshot(df: pd.DataFrame, *, config: EngineConfig | None = None) -> GexSnapshot:
    """Compute Net GEX, regime, and ZeroGEX for one validated chain snapshot."""
    net = net_gex(df, config=config)
    spot = float(df["underlying_price"].iloc[0]) if not df.empty else float("nan")
    return GexSnapshot(net_gex=net, regime=regime(net),
                       zero_gex=zero_gex(df, config=config), spot=spot)


__all__ = [
    "contract_gex",
    "net_gex",
    "gex_by_strike",
    "regime",
    "zero_gex",
    "GexSnapshot",
    "gamma_snapshot",
]
