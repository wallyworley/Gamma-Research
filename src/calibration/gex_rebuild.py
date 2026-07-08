"""Rebuild per-session Net GEX under the EMPIRICAL dealer-sign map (the payoff).

The third dealer-sign arm: instead of assuming a convention, sign each contract by
its stable-bucket empirical dealer sign, falling back to naive ``long_call_short_put``
for contracts whose bucket is unstable / insufficient / outside the DTE window. Net
GEX then uses the identical dollar-per-1%-move form as ``metrics.gex`` (reusing its
``dollar_factor`` and ``CONTRACT_SIZE`` so the arms are numerically comparable):

    contract GEX = sign * gamma * open_interest * 100 * (spot^2 * 0.01)

The fallback share (by gamma-weighted OI) is returned for every session so the
empirical arm's residual dependence on the naive assumption is always visible.

Also provides ``convention_agreement``: the gamma-weighted-OI share on which each
named convention's per-contract sign matches the empirical stable-bucket sign - how
close each assumption is to the measurement.
"""

from __future__ import annotations

from datetime import date

from ..metrics._common import CONTRACT_SIZE, dealer_signs, dollar_factor
from .bucket import CALL, PUT


def _session_of(chain) -> date:
    """The trading session (ET date) of a single-snapshot chain, from quote_ts."""
    ts = chain["quote_ts"].iloc[0]
    return ts.tz_convert("UTC").date() if ts.tzinfo else ts.date()


def _row_context(chain):
    """(spot_series, strike, gamma, oi, types, dte_days) as numpy arrays for a chain."""
    import numpy as np
    import pandas as pd

    session = _session_of(chain)
    spot = chain["underlying_price"].astype("float64").to_numpy()
    strike = chain["strike"].astype("float64").to_numpy()
    gamma = chain["gamma"].astype("float64").fillna(0.0).to_numpy()
    oi = chain["open_interest"].astype("float64").fillna(0.0).to_numpy()
    types = chain["type"].astype(str).to_numpy()
    exp = pd.to_datetime(chain["expiration"]).dt.tz_localize(None).dt.date
    dte = np.array([(e - session).days for e in exp], dtype="int64")
    return spot, strike, gamma, oi, types, dte


def _bucket_keys(spot, strike, types, dte):
    """Vectorized per-contract bucket key (str array); '' where outside the window.

    Mirrors bucket.bucket_for exactly (kept in lock-step by the unit tests) but over
    whole arrays, so the empirical rebuild over ~2,400 chains does not run a per-row
    Python loop.
    """
    import numpy as np

    m = np.divide(strike, spot, out=np.full_like(strike, np.nan, dtype="float64"),
                  where=spot > 0)
    mb = np.where(m <= 0.95, "<=0.95",
         np.where(m <= 0.99, "0.95-0.99",
         np.where(m <= 1.01, "0.99-1.01",
         np.where(m <= 1.05, "1.01-1.05", ">1.05"))))
    db = np.where(dte <= 7, "0-7", np.where(dte <= 30, "8-30", "31-60"))
    in_window = np.isfinite(m) & (strike > 0) & (spot > 0) & (dte >= 0) & (dte <= 60) \
        & np.isin(types, (CALL, PUT))
    keys = np.char.add(np.char.add(types.astype(str) + "|", mb.astype(str)), "|" + db.astype(str))
    keys = np.where(in_window, keys, "")
    return keys


def empirical_signs(chain, stable_lookup: dict[str, int]):
    """Per-contract (sign, is_fallback) arrays under the empirical map + fallback.

    A contract in a STABLE bucket takes that bucket's empirical dealer sign; every
    other contract (outside the DTE window, undefined moneyness, or unstable/
    insufficient bucket) takes the naive long_call_short_put fallback and is flagged.
    """
    import numpy as np

    spot, strike, _, _, types, dte = _row_context(chain)
    keys = _bucket_keys(spot, strike, types, dte)
    fb = np.where(types == CALL, 1.0, np.where(types == PUT, -1.0, 0.0))
    signs = fb.copy()
    is_fb = np.ones(len(chain), dtype=bool)
    if stable_lookup:
        emp = np.array([stable_lookup.get(k, 0) for k in keys], dtype="float64")
        hit = np.array([k in stable_lookup for k in keys], dtype=bool)
        signs = np.where(hit, emp, fb)
        is_fb = ~hit
    return signs, is_fb


def empirical_net_gex(chain, stable_lookup: dict[str, int]) -> dict:
    """Net GEX for one chain snapshot under the empirical sign map.

    Returns ``net_gex`` (dollar-per-1%-move), ``spot``, ``option_notional``,
    ``gex_norm`` (net_gex / option_notional, or None), and ``fallback_gamma_oi_frac``
    (share of |gamma*OI| signed by the naive fallback rather than a stable bucket).
    """
    import numpy as np

    if chain.empty:
        return {"net_gex": 0.0, "spot": float("nan"), "option_notional": 0.0,
                "gex_norm": None, "fallback_gamma_oi_frac": float("nan")}

    spot, strike, gamma, oi, types, dte = _row_context(chain)
    signs, is_fb = empirical_signs(chain, stable_lookup)
    factor = dollar_factor(spot, "dollar_per_1pct")
    contrib = signs * gamma * oi * CONTRACT_SIZE * factor
    net = float(np.sum(contrib))

    notional = float(np.sum(oi * CONTRACT_SIZE * spot))
    weight = np.abs(gamma * oi)
    wsum = float(weight.sum())
    fb_frac = float(weight[is_fb].sum() / wsum) if wsum > 0 else float("nan")
    return {
        "net_gex": net,
        "spot": float(np.median(spot)),
        "option_notional": notional,
        "gex_norm": (net / notional) if notional > 0 else None,
        "fallback_gamma_oi_frac": fb_frac,
    }


def convention_agreement(chain, stable_lookup: dict[str, int],
                         conventions=("long_call_short_put", "otm_customer")) -> dict:
    """Gamma-weighted-OI share where each convention's sign matches the empirical map.

    Only contracts that land in a STABLE bucket (a non-zero empirical sign) are scored -
    those are the cells where the measurement actually disagrees with, or confirms, an
    assumption. Returns ``{convention: matched_fraction}`` plus ``stable_gamma_oi_frac``
    (how much of the book the comparison covers).
    """
    import numpy as np

    if chain.empty:
        return {c: float("nan") for c in conventions} | {"stable_gamma_oi_frac": 0.0}

    spot, strike, gamma, oi, types, dte = _row_context(chain)
    emp_signs, is_fb = empirical_signs(chain, stable_lookup)
    weight = np.abs(gamma * oi)
    stable_mask = ~is_fb & (weight > 0)
    stable_w = float(weight[stable_mask].sum())
    total_w = float(weight.sum())

    out: dict[str, float] = {}
    for conv in conventions:
        conv_signs = dealer_signs(chain, conv)
        match = (np.sign(conv_signs) == np.sign(emp_signs)) & stable_mask
        out[conv] = float(weight[match].sum() / stable_w) if stable_w > 0 else float("nan")
    out["stable_gamma_oi_frac"] = (stable_w / total_w) if total_w > 0 else 0.0
    return out


__all__ = ["empirical_signs", "empirical_net_gex", "convention_agreement"]
