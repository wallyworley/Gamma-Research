"""Volatility-forecast harness (quant review item 3 / open finding F4).

The academically supported gamma-exposure effect is on *realized volatility*, not
on drift: dealer long-gamma hedging suppresses vol, short-gamma amplifies it. Vol
is persistent and forecastable, so this is the right FIRST experiment - it needs
far less history than a directional test and it cannot be laundered by beta.

The one question this harness answers honestly:

    does a per-date signal (e.g. normalized GEX) add next-day realized-vol
    forecast value BEYOND vol clustering?

The benchmark for "vol clustering" is a HAR-style model (Corsi 2009): today's
realized vol plus its trailing 5-day and 22-day means. A signal only earns credit
if it beats that. Two honesty devices guard the conclusion:

  * The forecast target is *strictly next-day*. `next_day_range` / `next_day_abs_return`
    place the OUTCOME of day t+1 on row t, and the HAR + signal regressors are all
    "as of t" (known at the close of t). Any misalignment is lookahead, so the
    alignment is documented loudly at every step and unit-tested by proving that a
    one-day forward shift of the signal (peeking) INCREASES the fitted R2.

  * In-sample incremental R2 is mechanically non-negative (a nested augmented model
    can never fit worse), so `augmented_r2 - baseline_r2 >= 0` proves nothing on its
    own. The significance check therefore uses the *adjusted* R2 increment, which
    penalizes the extra parameter and CAN go negative for a useless signal, plus a
    moving-block bootstrap that reports the fraction of resamples where that
    increment is <= 0 (a p-value-like honesty check). Naive OLS t-stats are also
    reported but flagged: vol-target autocorrelation inflates them, which is exactly
    why the block bootstrap exists.

Default target is `next_day_range` (the high-low range over the prior close): it is
the cleanest dealer-hedging-visible vol print (a dealer re-hedges against the path,
which the range captures far better than a single close-to-close return), it is
strictly positive, and it is robust to overnight-gap sign noise.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..config import EngineConfig
from ..metrics._common import resolve_config

_ANNUALIZE = math.sqrt(252.0)
_PARKINSON_C = 1.0 / (4.0 * math.log(2.0))   # 1 / (4 ln 2), the Parkinson constant

_MIN_OBS = 60           # below this many usable rows the scorecard returns NaNs
_BLOCK_LEN = 10         # moving-block bootstrap block length (~2 weeks of sessions)
_TARGETS = ("range", "abs_return", "parkinson")


# --------------------------------------------------------------------------- #
# Realized-vol estimators (building blocks; golden-tested)
# --------------------------------------------------------------------------- #

def realized_vol_cc(bars: pd.DataFrame, window: int) -> pd.Series:
    """Close-to-close realized volatility, annualized (sqrt(252)).

    Rolling sample std (ddof=1) of daily log returns ln(C_t / C_{t-1}) over
    ``window`` returns, times sqrt(252). The value at row t uses returns through t
    only (no lookahead); the first ``window`` rows are NaN (an incomplete window).
    """
    close = bars["close"].astype("float64")
    logret = np.log(close / close.shift(1))
    return logret.rolling(window).std(ddof=1) * _ANNUALIZE


def realized_vol_parkinson(bars: pd.DataFrame, window: int) -> pd.Series:
    """Parkinson high-low realized volatility, annualized (sqrt(252)).

        Parkinson = sqrt( (1/(4 ln 2)) * mean( ln(H/L)^2 ) ) * sqrt(252)

    The Parkinson estimator uses the intraday range and so is ~5x more efficient
    than close-to-close for a given window. Rows with a null or non-positive high
    or low have an undefined ln(H/L) and are DROPPED from the window mean (the mean
    is taken over the valid rows only, min_periods=1); a window with no valid row is
    NaN. The value at row t uses ranges through t only (no lookahead).
    """
    high = bars["high"].astype("float64")
    low = bars["low"].astype("float64")
    valid = high.notna() & low.notna() & (high > 0) & (low > 0)
    hl2 = (np.log(high.where(valid) / low.where(valid)) ** 2)

    def _nanmean(a: np.ndarray) -> float:
        a = a[~np.isnan(a)]
        return float("nan") if a.size == 0 else float(a.mean())

    mean_hl2 = hl2.rolling(window, min_periods=1).apply(_nanmean, raw=True)
    return np.sqrt(_PARKINSON_C * mean_hl2) * _ANNUALIZE


# --------------------------------------------------------------------------- #
# Forecast targets (the OUTCOME of t+1, placed on row t)
# --------------------------------------------------------------------------- #
#
# ALIGNMENT (read this): every target below is shifted so that the value on row t
# is realized on day t+1. The regressors (HAR features and the signal) are all
# "as of t". So a regression target[t] ~ features[t] predicts t+1 strictly from
# information available at the close of t. Do NOT regress a target against a
# CONTEMPORANEOUS (unshifted) vol series - that is lookahead. The last row of every
# target is NaN (t+1 does not exist yet).

def next_day_abs_return(bars: pd.DataFrame) -> pd.Series:
    """|next day's close-to-close simple return|, aligned so row t = outcome of t+1.

    next_day_abs_return[t] = |C_{t+1}/C_t - 1|. The absolute value makes it a
    (direction-free) volatility print. Last row is NaN.
    """
    close = bars["close"].astype("float64")
    same_day = (close / close.shift(1) - 1.0).abs()   # |return realized ON day d|
    return same_day.shift(-1)                          # ...moved onto the prior row


def next_day_range(bars: pd.DataFrame) -> pd.Series:
    """Next day's high-low range over the prior close, aligned so row t = outcome of t+1.

    next_day_range[t] = (H_{t+1} - L_{t+1}) / C_t. Normalizing by the PRIOR close
    (C_t, the close before the range day) keeps it a clean, gap-inclusive vol print
    known only after t+1. Last row is NaN.
    """
    high = bars["high"].astype("float64")
    low = bars["low"].astype("float64")
    prior_close = bars["close"].astype("float64").shift(1)
    same_day = (high - low) / prior_close              # range realized ON day d, over C_{d-1}
    return same_day.shift(-1)                           # ...moved onto the prior row


def _next_day_parkinson(bars: pd.DataFrame) -> pd.Series:
    """Single-day Parkinson vol of t+1, placed on row t (the 'parkinson' target)."""
    same_day = realized_vol_parkinson(bars, window=1)   # per-day Parkinson (window=1)
    return same_day.shift(-1)


def _same_day_measure(bars: pd.DataFrame, target: str) -> pd.Series:
    """The as-of-t daily vol measure whose one-day-ahead value IS the target.

    Used as the HAR base series so the clustering baseline is the persistence of the
    exact quantity being forecast: measure[t] is known at the close of t, and
    target[t] == measure[t+1].
    """
    high = bars["high"].astype("float64")
    low = bars["low"].astype("float64")
    close = bars["close"].astype("float64")
    if target == "range":
        return (high - low) / close.shift(1)
    if target == "abs_return":
        return (close / close.shift(1) - 1.0).abs()
    if target == "parkinson":
        return realized_vol_parkinson(bars, window=1)
    raise ValueError(f"unknown target {target!r}; expected one of {_TARGETS}")


def _target_series(bars: pd.DataFrame, target: str) -> pd.Series:
    if target == "range":
        return next_day_range(bars)
    if target == "abs_return":
        return next_day_abs_return(bars)
    if target == "parkinson":
        return _next_day_parkinson(bars)
    raise ValueError(f"unknown target {target!r}; expected one of {_TARGETS}")


# --------------------------------------------------------------------------- #
# HAR baseline
# --------------------------------------------------------------------------- #

def har_features(rv_daily: pd.Series) -> pd.DataFrame:
    """Classic HAR-RV regressors from a daily realized-vol series (Corsi 2009).

    Columns, all "as of t" (computed from rv_daily through t inclusive):

        RV_d = rv_daily[t]                      today's realized vol
        RV_w = mean(rv_daily[t-4 .. t])         trailing 5-day (weekly) mean
        RV_m = mean(rv_daily[t-21 .. t])        trailing 22-day (monthly) mean

    From the perspective of the predicted day t+1 these are all LAGGED (yesterday's
    daily / weekly / monthly vol), so pairing them with a forward-shifted target
    (`next_day_*`) predicts t+1 using only information available at t. Regressing
    them against a CONTEMPORANEOUS (unshifted) rv_daily would be lookahead - do not.
    The leading rows (before 22 observations exist) carry NaNs.
    """
    rv = rv_daily.astype("float64")
    return pd.DataFrame({
        "RV_d": rv,
        "RV_w": rv.rolling(5).mean(),
        "RV_m": rv.rolling(22).mean(),
    })


# --------------------------------------------------------------------------- #
# OLS helpers
# --------------------------------------------------------------------------- #

def _ols_r2(y: np.ndarray, X: np.ndarray) -> float:
    """In-sample R2 of an OLS fit y ~ X (X must include an intercept column)."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    if ss_tot <= 0.0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def _adjusted_r2(r2: float, n: int, k: int) -> float:
    """Adjusted R2 (k = number of regressors incl. intercept). Penalizes free params.

    Unlike plain R2, this CAN fall when a useless regressor is added, which is what
    makes the incremental-adjusted-R2 an honest significance signal.
    """
    if not np.isfinite(r2) or n - k <= 0:
        return float("nan")
    return 1.0 - (1.0 - r2) * (n - 1) / (n - k)


def _ols_full(y: np.ndarray, X: np.ndarray) -> dict:
    """OLS fit with R2 and classic t-stats. X includes an intercept column."""
    n, k = X.shape
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = float("nan") if ss_tot <= 0.0 else 1.0 - ss_res / ss_tot
    tstats = np.full(k, np.nan)
    if n - k > 0:
        sigma2 = ss_res / (n - k)
        xtx_inv = np.linalg.pinv(X.T @ X)                       # pinv: robust to rank issues
        se = np.sqrt(np.maximum(np.diag(sigma2 * xtx_inv), 0.0))
        with np.errstate(divide="ignore", invalid="ignore"):
            tstats = np.where(se > 0, beta / se, np.nan)
    return {"beta": beta, "r2": r2, "adj_r2": _adjusted_r2(r2, n, k), "tstats": tstats}


def _incremental_adj_r2(y: np.ndarray, Xb: np.ndarray, Xa: np.ndarray) -> float:
    """Adjusted-R2 increment of the augmented model over the baseline on one sample."""
    n = y.shape[0]
    adj_b = _adjusted_r2(_ols_r2(y, Xb), n, Xb.shape[1])
    adj_a = _adjusted_r2(_ols_r2(y, Xa), n, Xa.shape[1])
    return adj_a - adj_b


def _moving_block_bootstrap_inc(y: np.ndarray, Xb: np.ndarray, Xa: np.ndarray, *,
                                n_bootstrap: int, block_len: int, seed: int) -> np.ndarray:
    """Moving-block bootstrap of the adjusted-R2 increment.

    Resamples contiguous blocks of ROWS jointly (target and both design matrices
    together), preserving the local autocorrelation that inflates naive t-stats,
    refits both models on each resample, and returns the vector of increments.
    """
    n = y.shape[0]
    if n <= block_len or n_bootstrap <= 0:
        return np.array([])
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block_len))
    max_start = n - block_len                                    # inclusive upper start index
    offsets = np.arange(block_len)
    out = np.empty(n_bootstrap)
    for b in range(n_bootstrap):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        idx = (starts[:, None] + offsets).ravel()[:n]           # concatenate blocks, trim to n
        out[b] = _incremental_adj_r2(y[idx], Xb[idx], Xa[idx])
    return out


# --------------------------------------------------------------------------- #
# The scorecard
# --------------------------------------------------------------------------- #

def _nan_scorecard(cfg: EngineConfig, target: str, n_obs: int) -> dict:
    """Clean NaN result for insufficient data (never raise on a short history)."""
    nan = float("nan")
    return {
        "config_hash": cfg.config_hash(),
        "target": target,
        "n_obs": int(n_obs),
        "insufficient_data": True,
        "baseline_r2": nan, "augmented_r2": nan, "incremental_r2": nan,
        "baseline_r2_adj": nan, "augmented_r2_adj": nan, "incremental_r2_adj": nan,
        "signal_coef": nan, "signal_tstat": nan,
        "bootstrap": {"n": 0, "block_length": _BLOCK_LEN,
                      "incremental_r2_adj_ci95": (nan, nan), "frac_incremental_le_0": nan},
        "sign_consistency": {"signed": False, "corr": nan, "corr_sign": 0, "subsample": None},
    }


def _sign_consistency(y: np.ndarray, Xb: np.ndarray, Xa: np.ndarray,
                      signal: np.ndarray) -> dict:
    """Correlation sign of signal vs target, and the regression split by signal sign.

    If the signal is one-sided (only >=0 or only <=0), the subsample split is skipped
    cleanly (``subsample=None``); a signed signal reports the incremental adjusted R2
    on each side so a spurious whole-sample fit driven by one sign is visible.
    """
    with np.errstate(invalid="ignore"):
        corr = float(np.corrcoef(signal, y)[0, 1]) if signal.std() > 0 and y.std() > 0 else float("nan")
    corr_sign = int(np.sign(corr)) if np.isfinite(corr) else 0

    pos, neg = signal > 0, signal < 0
    signed = bool(pos.any() and neg.any())
    subsample = None
    if signed:
        subsample = {}
        for name, mask in (("positive", pos), ("negative", neg)):
            if int(mask.sum()) >= 30:
                subsample[name] = {
                    "n": int(mask.sum()),
                    "incremental_r2_adj": _incremental_adj_r2(y[mask], Xb[mask], Xa[mask]),
                }
            else:
                subsample[name] = {"n": int(mask.sum()), "incremental_r2_adj": float("nan")}
    return {"signed": signed, "corr": corr, "corr_sign": corr_sign, "subsample": subsample}


def vol_forecast_scorecard(bars: pd.DataFrame, signal, *,
                           config: EngineConfig | None = None,
                           target: str = "range",
                           n_bootstrap: int = 1000, seed: int = 0) -> dict:
    """Does ``signal`` add next-day vol-forecast value beyond HAR vol clustering?

    ``signal`` is a per-date series aligned to ``bars.index`` (e.g. normalized Net
    GEX). Its value on date t must be known at the CLOSE of t; it is used as-of-t to
    predict the target realized on t+1 (no shift applied here - the forward shift
    that makes this a forecast lives entirely in the ``next_day_*`` target). The
    baseline is HAR (today's / weekly / monthly realized vol of the same measure);
    the augmented model adds the signal.

    Returns a dict with (a) baseline & augmented R2, (b) the plain incremental R2
    (>= 0 in-sample - reported but NOT a significance test) and the signal's OLS
    t-stat, (c) the ADJUSTED incremental R2 with a moving-block-bootstrap 95% CI and
    the fraction of resamples where it is <= 0 (the honest p-value-like check), and
    (d) sign-consistency (corr sign; per-sign-subsample fits if the signal is
    signed). Stamped with config_hash(). With fewer than ~60 usable rows it returns
    a clean NaN scorecard rather than raising.
    """
    cfg = resolve_config(config)
    if target not in _TARGETS:
        raise ValueError(f"unknown target {target!r}; expected one of {_TARGETS}")

    y_full = _target_series(bars, target)
    measure = _same_day_measure(bars, target)
    har = har_features(measure)
    sig = pd.Series(signal).reindex(bars.index).astype("float64")

    frame = pd.concat(
        [y_full.rename("y"), har, sig.rename("signal")], axis=1
    ).replace([np.inf, -np.inf], np.nan).dropna()
    n_obs = int(len(frame))
    if n_obs < _MIN_OBS:
        return _nan_scorecard(cfg, target, n_obs)

    y = frame["y"].to_numpy()
    har_cols = frame[["RV_d", "RV_w", "RV_m"]].to_numpy()
    signal_col = frame["signal"].to_numpy()
    ones = np.ones((n_obs, 1))
    Xb = np.hstack([ones, har_cols])                       # baseline: 1 + HAR
    Xa = np.hstack([Xb, signal_col[:, None]])              # augmented: + signal (last column)

    base = _ols_full(y, Xb)
    aug = _ols_full(y, Xa)
    inc_r2 = aug["r2"] - base["r2"]
    inc_adj = aug["adj_r2"] - base["adj_r2"]

    boot = _moving_block_bootstrap_inc(
        y, Xb, Xa, n_bootstrap=n_bootstrap, block_len=_BLOCK_LEN, seed=seed)
    if boot.size:
        lo, hi = (float(x) for x in np.quantile(boot, [0.025, 0.975]))
        frac_le_0 = float(np.mean(boot <= 0.0))
    else:
        lo = hi = frac_le_0 = float("nan")

    return {
        "config_hash": cfg.config_hash(),
        "target": target,
        "n_obs": n_obs,
        "insufficient_data": False,
        "baseline_r2": base["r2"],
        "augmented_r2": aug["r2"],
        "incremental_r2": inc_r2,                           # >= 0 in-sample (not a test)
        "baseline_r2_adj": base["adj_r2"],
        "augmented_r2_adj": aug["adj_r2"],
        "incremental_r2_adj": inc_adj,                      # the significance-relevant increment
        "signal_coef": float(aug["beta"][-1]),
        "signal_tstat": float(aug["tstats"][-1]),           # naive OLS; autocorr inflates it
        "bootstrap": {
            "n": int(boot.size),
            "block_length": _BLOCK_LEN,
            "incremental_r2_adj_ci95": (lo, hi),
            "frac_incremental_le_0": frac_le_0,
        },
        "sign_consistency": _sign_consistency(y, Xb, Xa, signal_col),
    }


__all__ = [
    "realized_vol_cc",
    "realized_vol_parkinson",
    "next_day_abs_return",
    "next_day_range",
    "har_features",
    "vol_forecast_scorecard",
]
