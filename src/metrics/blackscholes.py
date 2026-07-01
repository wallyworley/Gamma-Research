"""Black-Scholes gamma (the only greek the metric engine recomputes itself).

Net GEX / regime use the *vendor's* reported gamma at the snapshot. ZeroGEX,
however, is the spot S where net dealer gamma flips sign, and that requires
recomputing each option's gamma as S varies (docs/reddit_gamma_strategy_terms.md
"ZeroGEX ... recomputing the chain's aggregate gamma at candidate prices").
Holding vendor gamma fixed cannot flip the sign - the dollar weighting S^2*0.01
is a positive scalar common to every term - so a pricer is unavoidable here.

Gamma is identical for a call and a put at the same (K, T, sigma), so only one
formula is needed; the call/put sign is applied by the GEX layer, not here.

    gamma = exp(-q*T) * pdf(d1) / (S * sigma * sqrt(T))
    d1    = (ln(S/K) + (r - q + sigma^2/2) * T) / (sigma * sqrt(T))

r, q, and the day count come from PricerConfig (pinned in M0).
"""

from __future__ import annotations

import numpy as np

_INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)


def _norm_pdf(x: np.ndarray) -> np.ndarray:
    return _INV_SQRT_2PI * np.exp(-0.5 * x * x)


def bs_gamma(S, K, T, sigma, r: float = 0.0, q: float = 0.0):
    """Black-Scholes gamma. Vectorized; scalars return a float.

    Contracts with non-positive T or sigma have undefined gamma and return 0.0
    (an expired or vol-less option contributes no re-hedging gamma).
    """
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)

    valid = (S > 0) & (K > 0) & (T > 0) & (sigma > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_root_t = sigma * np.sqrt(T)
        d1 = (np.log(np.where(valid, S / K, 1.0)) + (r - q + 0.5 * sigma * sigma) * T) / vol_root_t
        gamma = np.exp(-q * T) * _norm_pdf(d1) / (S * vol_root_t)

    gamma = np.where(valid, gamma, 0.0)
    return float(gamma) if gamma.ndim == 0 else gamma


__all__ = ["bs_gamma"]
