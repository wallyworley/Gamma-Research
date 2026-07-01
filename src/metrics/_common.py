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


__all__ = ["CONTRACT_SIZE", "DEALER_SIGNS", "resolve_config", "dealer_signs", "dollar_factor"]
