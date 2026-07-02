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

# Per-type dealer sign. long_call_short_put is the terms-doc standard: dealers
# net long calls (+1), net short puts (-1).
DEALER_SIGNS = {
    "long_call_short_put": {"call": 1.0, "put": -1.0},
    "short_call_long_put": {"call": -1.0, "put": 1.0},
}


def resolve_config(config: EngineConfig | None) -> EngineConfig:
    return config if config is not None else EngineConfig.default()


def dealer_signs(df: pd.DataFrame, convention: str) -> np.ndarray:
    """Per-row dealer sign (+1/-1) from the option type under ``convention``."""
    try:
        mapping = DEALER_SIGNS[convention]
    except KeyError:
        raise ValueError(
            f"unknown dealer_sign_convention {convention!r}; "
            f"known: {sorted(DEALER_SIGNS)}") from None
    return df["type"].map(mapping).fillna(0.0).to_numpy(dtype=float)


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


__all__ = ["CONTRACT_SIZE", "DEALER_SIGNS", "resolve_config", "dealer_signs", "dollar_factor",
           "require_single_snapshot", "greek_coverage"]
