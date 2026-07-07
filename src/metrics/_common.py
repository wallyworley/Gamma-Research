"""Shared metric primitives: dealer sign, contract size, config, dollar factor.

Both GEX (gex.py) and the M3 proxy suite (dex.py, ratios.py, levels.py) apply
the same unobservable dealer-sign convention and contract multiplier. Keeping
them here means the convention is defined once, so GEX and DEX can never drift
apart (terms doc "Foundational caveat": every dealer-positioning number rides on
this one assumption).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import EngineConfig

CONTRACT_SIZE = 100

# Calendar-day conventions for time-to-expiry (act/365 is the pinned default).
# Single-sourced here so every greek recompute (ZeroGEX, vanna, charm) shares one
# day count and cannot drift.
DAY_COUNTS = {"act/365": 365.0, "act/365.25": 365.25}

# Per-type dealer sign. long_call_short_put is the terms-doc standard: dealers
# net long calls (+1), net short puts (-1). "otm_customer" is not a simple
# type -> sign map (it needs strike vs spot per row) and is handled specially in
# dealer_signs() below.
DEALER_SIGNS = {
    "long_call_short_put": {"call": 1.0, "put": -1.0},
    "short_call_long_put": {"call": -1.0, "put": 1.0},
}

# Conventions handled outside the DEALER_SIGNS type-map (spot-aware, per row).
_SPOT_AWARE_CONVENTIONS = ("otm_customer",)


def resolve_config(config: EngineConfig | None) -> EngineConfig:
    return config if config is not None else EngineConfig.default()


def _otm_customer_signs(df: pd.DataFrame) -> np.ndarray:
    """Dealer sign under the skew-adjusted "otm_customer" convention (review item 5).

    The naive long-call/short-put assumption has been retired across the field; this
    is one transparent alternative. It keeps the OTM direction of the naive model but
    *excludes* in-the-money open interest instead of guessing its origin:

      * OTM puts  (strike < spot): dealer sign -1  (customers buy protection)
      * OTM calls (strike > spot): dealer sign +1  (customers buy upside)
      * ITM options (and exactly-at-spot): sign 0, excluded.

    ITM open interest is dropped because its origin is genuinely ambiguous: it is
    mostly early-exercise leftovers and inter-dealer inventory, not a clean
    customer-vs-dealer signal, so assigning it either sign injects noise. Requires
    ``underlying_price`` (a required schema column) for the per-row spot.
    """
    spot = df["underlying_price"].astype("float64").to_numpy()
    strike = df["strike"].astype("float64").to_numpy()
    typ = df["type"].to_numpy()
    signs = np.zeros(len(df), dtype=float)
    signs[(typ == "call") & (strike > spot)] = 1.0
    signs[(typ == "put") & (strike < spot)] = -1.0
    return signs


def dealer_signs(df: pd.DataFrame, convention: str) -> np.ndarray:
    """Per-row dealer sign from the option type (and spot, for spot-aware conventions)."""
    if convention == "otm_customer":
        return _otm_customer_signs(df)
    try:
        mapping = DEALER_SIGNS[convention]
    except KeyError:
        known = sorted(list(DEALER_SIGNS) + list(_SPOT_AWARE_CONVENTIONS))
        raise ValueError(
            f"unknown dealer_sign_convention {convention!r}; known: {known}") from None
    return df["type"].map(mapping).fillna(0.0).to_numpy(dtype=float)


def years_to_expiry(df: pd.DataFrame, day_count: str) -> np.ndarray:
    """Calendar years from each row's quote date to its expiration, under ``day_count``.

    Quote date is the UTC calendar date of ``quote_ts`` (EOD snapshots quote near
    20:00 UTC, so this equals the ET session date). Shared by every greek recompute
    so the time basis is identical across GEX, vanna, and charm.
    """
    try:
        denom = DAY_COUNTS[day_count]
    except KeyError:
        raise NotImplementedError(
            f"day_count {day_count!r} not supported; known: {sorted(DAY_COUNTS)}") from None
    qd = df["quote_ts"].dt.tz_convert("UTC").dt.date
    ed = df["expiration"].dt.date
    days = np.array([(e - d).days for e, d in zip(ed, qd)], dtype=float)
    return days / denom


def dollar_factor(spot, gex_convention: str):
    """Weight applied on top of the share form. ``spot`` may be scalar or array."""
    if gex_convention == "dollar_per_1pct":
        return np.asarray(spot, dtype=float) ** 2 * 0.01
    if gex_convention == "shares":
        return 1.0
    raise ValueError(f"unknown gex_convention {gex_convention!r}; known: dollar_per_1pct, shares")


def require_single_snapshot(df: pd.DataFrame) -> None:
    """Guard: a snapshot metric expects ONE (symbol, quote_ts). A concatenated
    multi-day / multi-symbol frame (an easy `pd.concat` mistake) would silently
    mix snapshots and compute nonsense - fail loudly instead (F17)."""
    if df.empty:
        return
    n_sym = df["symbol"].nunique()
    n_ts = df["quote_ts"].nunique()
    if n_sym > 1 or n_ts > 1:
        raise ValueError(
            f"snapshot metric expects a single (symbol, quote_ts); got {n_sym} symbol(s) x "
            f"{n_ts} timestamp(s). Slice to one snapshot before computing.")


def greek_coverage(df: pd.DataFrame) -> dict:
    """Data-quality summary for a snapshot (F12): the share of open interest backed
    by usable greeks/IV. A GEX/ZeroGEX computed where coverage is low is only as
    trustworthy as the fraction of OI that actually carried greeks.
    """
    if df.empty:
        return {"n_contracts": 0, "oi_total": 0.0, "oi_gamma_frac": float("nan"),
                "oi_iv_frac": float("nan"), "n_iv_zero": 0}
    oi = df["open_interest"].astype("float64").fillna(0.0)
    gamma = df["gamma"].astype("float64")
    iv = df["iv"].astype("float64")
    total = float(oi.sum())

    def frac(mask) -> float:
        return (float(oi[mask].sum()) / total) if total > 0 else float("nan")

    return {
        "n_contracts": int(len(df)),
        "oi_total": total,
        "oi_gamma_frac": frac(gamma.notna() & (gamma.abs() > 0)),  # OI share with nonzero gamma
        "oi_iv_frac": frac(iv.notna() & (iv > 0)),                 # OI share with iv > 0
        "n_iv_zero": int((iv.fillna(-1.0) == 0).sum()),
    }


__all__ = ["CONTRACT_SIZE", "DAY_COUNTS", "DEALER_SIGNS", "resolve_config", "dealer_signs",
           "years_to_expiry", "dollar_factor", "require_single_snapshot", "greek_coverage"]
