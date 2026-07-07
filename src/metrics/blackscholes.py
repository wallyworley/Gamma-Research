"""Black-Scholes greeks the metric engine recomputes itself: gamma, vanna, charm.

Net GEX / regime use the *vendor's* reported gamma at the snapshot. ZeroGEX,
however, is the spot S where net dealer gamma flips sign, and that requires
recomputing each option's gamma as S varies (docs/reddit_gamma_strategy_terms.md
"ZeroGEX ... recomputing the chain's aggregate gamma at candidate prices").
Holding vendor gamma fixed cannot flip the sign - the dollar weighting S^2*0.01
is a positive scalar common to every term - so a pricer is unavoidable here.

Second-order flow greeks (vanna, charm) drive the documented OpEx/expiration
effects (quant review item 6). The vendor snapshot carries delta/vega/theta but
not vanna/charm, so this module is the single Black-Scholes home that computes
them, under the same PricerConfig r/q/day-count conventions used everywhere else.

Common terms (r = risk-free, q = continuous dividend yield, T in years):

    d1    = (ln(S/K) + (r - q + sigma^2/2) * T) / (sigma * sqrt(T))
    d2    = d1 - sigma * sqrt(T)

    gamma = exp(-q*T) * pdf(d1) / (S * sigma * sqrt(T))            # d^2V/dS^2

    vanna = -exp(-q*T) * pdf(d1) * d2 / sigma                      # dDelta/dsigma
            (per 1.00 change in sigma; call and put share it, like gamma)

    charm_call = q*exp(-q*T)*N(d1)  - exp(-q*T)*pdf(d1)*(2(r-q)T - d2*sigma*sqrt(T))
                                     / (2*T*sigma*sqrt(T))         # dDelta/d(cal.time)
    charm_put  = -q*exp(-q*T)*N(-d1) - [same second term]
            (delta per YEAR of calendar time passing = -dDelta/dT; differs by
             call/put only through the q*exp(-q*T)*N(+/-d1) term, so it vanishes
             when q = 0)

Gamma and vanna are identical for a call and a put at the same (K, T, sigma);
charm is not. The dealer-position call/put SIGN is applied by the exposure layer
(gex.py / vanna_charm.py), never here.

r, q, and the day count come from PricerConfig (pinned in M0).
"""

from __future__ import annotations

import numpy as np
from scipy.special import ndtr  # vectorized standard-normal CDF

_INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)


def _norm_pdf(x: np.ndarray) -> np.ndarray:
    return _INV_SQRT_2PI * np.exp(-0.5 * x * x)


def _d1_d2(S, K, T, sigma, r: float, q: float):
    """d1, d2, sqrt(T), and the validity mask, sharing one guarded computation."""
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    valid = (S > 0) & (K > 0) & (T > 0) & (sigma > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        root_t = np.sqrt(T)
        vol_root_t = sigma * root_t
        d1 = (np.log(np.where(valid, S / K, 1.0)) + (r - q + 0.5 * sigma * sigma) * T) / vol_root_t
        d2 = d1 - vol_root_t
    return S, K, T, sigma, d1, d2, root_t, valid


def _scalarize(arr: np.ndarray):
    return float(arr) if arr.ndim == 0 else arr


def bs_gamma(S, K, T, sigma, r: float = 0.0, q: float = 0.0):
    """Black-Scholes gamma. Vectorized; scalars return a float.

    Contracts with non-positive T or sigma have undefined gamma and return 0.0
    (an expired or vol-less option contributes no re-hedging gamma).
    """
    S, K, T, sigma, d1, _d2, root_t, valid = _d1_d2(S, K, T, sigma, r, q)
    with np.errstate(divide="ignore", invalid="ignore"):
        gamma = np.exp(-q * T) * _norm_pdf(d1) / (S * sigma * root_t)
    return _scalarize(np.where(valid, gamma, 0.0))


def bs_vanna(S, K, T, sigma, r: float = 0.0, q: float = 0.0):
    """Black-Scholes vanna = dDelta/dsigma = dVega/dS. Vectorized.

        vanna = -exp(-q*T) * pdf(d1) * d2 / sigma

    Per 1.00 change in sigma (i.e. multiply by 0.01 for a per-vol-point figure).
    Identical for calls and puts (delta_call - delta_put = exp(-q*T), constant in
    sigma). Non-positive T or sigma return 0.0.
    """
    S, K, T, sigma, d1, d2, _root_t, valid = _d1_d2(S, K, T, sigma, r, q)
    with np.errstate(divide="ignore", invalid="ignore"):
        vanna = -np.exp(-q * T) * _norm_pdf(d1) * d2 / sigma
    return _scalarize(np.where(valid, vanna, 0.0))


def bs_charm(S, K, T, sigma, r: float = 0.0, q: float = 0.0, is_call=True):
    """Black-Scholes charm = dDelta/d(calendar time) = -dDelta/dT. Vectorized.

    Returned in delta per YEAR of calendar time passing (T is in years). ``is_call``
    (bool or array broadcast to the inputs) selects the call vs put branch, which
    differ only in the q*exp(-q*T)*N(+/-d1) term:

        charm_call =  q*exp(-q*T)*N(d1)  - second_term
        charm_put  = -q*exp(-q*T)*N(-d1) - second_term
        second_term = exp(-q*T)*pdf(d1)*(2*(r-q)*T - d2*sigma*sqrt(T)) / (2*T*sigma*sqrt(T))

    Non-positive T or sigma return 0.0.
    """
    S, K, T, sigma, d1, d2, root_t, valid = _d1_d2(S, K, T, sigma, r, q)
    is_call = np.asarray(is_call, dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        disc = np.exp(-q * T)
        second = disc * _norm_pdf(d1) * (2.0 * (r - q) * T - d2 * sigma * root_t) / (2.0 * T * sigma * root_t)
        first = np.where(is_call, q * disc * ndtr(d1), -q * disc * ndtr(-d1))
        charm = first - second
    # np.where broadcasts valid against charm (e.g. scalar S/K with an is_call array).
    return _scalarize(np.where(valid, charm, 0.0))


__all__ = ["bs_gamma", "bs_vanna", "bs_charm"]
