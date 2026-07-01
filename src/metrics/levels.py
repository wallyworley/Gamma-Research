"""Open-interest concentration levels and gamma-transition proxies.

Grounded in the terms doc:
  * COI / POI - argmax-OI call / put strike (known acronym; the level use is an
    inferred GammaEdge implementation).
  * The C_TM_ moneyness grid (proxy) - OI concentration by type x moneyness:

        | Below spot        | Above spot        |
        | Puts  COTMP (OTM) | CITMP (ITM)       |
        | Calls CITMC (ITM) | COTMC (OTM)       |

  * PTrans / NTrans (proxy) - the first strike above / below spot where per-strike
    call vs put gamma dominance flips (an acceleration trigger, not support/
    resistance). GammaEdge's exact rolling window/weighting is unpublished; this
    is a transparent per-strike-dominance baseline.

OI is used as reported; T-1 alignment is the backtester's job (M4).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config import EngineConfig
from ._common import resolve_config
from .gex import contract_gex


def _oi_by_strike(df: pd.DataFrame) -> pd.Series:
    """Total open interest per strike (float, zero-filled), sorted by strike."""
    oi = df["open_interest"].astype("float64").fillna(0.0)
    return oi.groupby(df["strike"]).sum().sort_index()


def _argmax_oi_strike(df: pd.DataFrame) -> float | None:
    """Strike carrying the most open interest in ``df`` (None if empty/all-zero)."""
    if df.empty:
        return None
    by = _oi_by_strike(df)
    by = by[by > 0]
    return None if by.empty else float(by.idxmax())


@dataclass(frozen=True)
class OiLevels:
    """Call/put OI concentration levels and aggregate OI."""

    coi_level: float | None   # strike with the most call OI
    poi_level: float | None   # strike with the most put OI
    coi_total: float          # aggregate call OI
    poi_total: float          # aggregate put OI


def oi_levels(df: pd.DataFrame) -> OiLevels:
    """COI/POI: max-OI call and put strikes, plus aggregate call/put OI."""
    calls = df[df["type"] == "call"]
    puts = df[df["type"] == "put"]
    return OiLevels(
        coi_level=_argmax_oi_strike(calls),
        poi_level=_argmax_oi_strike(puts),
        coi_total=float(calls["open_interest"].astype("float64").fillna(0.0).sum()),
        poi_total=float(puts["open_interest"].astype("float64").fillna(0.0).sum()),
    )


@dataclass(frozen=True)
class MoneynessLevels:
    """The four C_TM_ OI-concentration levels (all proxies), as strikes or None."""

    cotmp_proxy: float | None   # OTM puts (strike < spot) - downside
    cotmc_proxy: float | None   # OTM calls (strike > spot) - upside
    citmp_proxy: float | None   # ITM puts (strike > spot) - upside
    citmc_proxy: float | None   # ITM calls (strike < spot) - downside


def moneyness_levels(df: pd.DataFrame) -> MoneynessLevels:
    """COTMP/COTMC/CITMP/CITMC: max-OI strike in each type x moneyness bucket."""
    if df.empty:
        return MoneynessLevels(None, None, None, None)
    spot = float(df["underlying_price"].iloc[0])
    calls = df[df["type"] == "call"]
    puts = df[df["type"] == "put"]
    strike = df["strike"].astype("float64")
    below = strike < spot
    above = strike > spot
    return MoneynessLevels(
        cotmp_proxy=_argmax_oi_strike(puts[puts["strike"].astype("float64") < spot]),
        cotmc_proxy=_argmax_oi_strike(calls[calls["strike"].astype("float64") > spot]),
        citmp_proxy=_argmax_oi_strike(puts[puts["strike"].astype("float64") > spot]),
        citmc_proxy=_argmax_oi_strike(calls[calls["strike"].astype("float64") < spot]),
    )


@dataclass(frozen=True)
class Transitions:
    """PTrans/NTrans gamma-dominance transition strikes (proxies)."""

    ptrans_proxy: float | None   # first strike above spot where call gamma dominates
    ntrans_proxy: float | None   # first strike below spot where put gamma dominates


def gamma_transitions(df: pd.DataFrame, *, config: EngineConfig | None = None) -> Transitions:
    """PTrans/NTrans: nearest strikes where per-strike call/put gamma dominance flips."""
    cfg = resolve_config(config)
    if df.empty:
        return Transitions(None, None)

    spot = float(df["underlying_price"].iloc[0])
    gex = contract_gex(df, config=cfg).abs()
    is_call = df["type"] == "call"
    call_mag = gex[is_call].groupby(df["strike"][is_call]).sum()
    put_mag = gex[~is_call].groupby(df["strike"][~is_call]).sum()

    strikes = sorted(set(df["strike"].astype("float64")))
    dominance = {k: float(call_mag.get(k, 0.0)) - float(put_mag.get(k, 0.0)) for k in strikes}

    above = [k for k in strikes if k > spot and dominance[k] > 0]
    below = [k for k in strikes if k < spot and dominance[k] < 0]
    return Transitions(
        ptrans_proxy=min(above) if above else None,   # lowest call-dominated strike above spot
        ntrans_proxy=max(below) if below else None,   # highest put-dominated strike below spot
    )


__all__ = [
    "OiLevels", "oi_levels",
    "MoneynessLevels", "moneyness_levels",
    "Transitions", "gamma_transitions",
]
