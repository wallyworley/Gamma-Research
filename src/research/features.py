"""Point-in-time feature construction for EXP-2026-001 (no forward targets)."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pandas as pd

from ..calibration.gex_rebuild import empirical_signs
from ..config import EngineConfig
from ..metrics._common import CONTRACT_SIZE, dollar_factor, years_to_expiry
from ..metrics.blackscholes import bs_gamma
from ..metrics.expiry import days_to_monthly_opex, oi_expiring_within
from ..metrics.flow import option_notional
from ..metrics.gex import contract_gex_recomputed


def _interp_30(points: list[tuple[int, float]]) -> float:
    points = sorted((d, v) for d, v in points if math.isfinite(v))
    if not points:
        return float("nan")
    exact = [v for d, v in points if d == 30]
    if exact:
        return exact[0]
    lo = [(d, v) for d, v in points if d < 30]
    hi = [(d, v) for d, v in points if d > 30]
    if lo and hi:
        d0, v0 = lo[-1]; d1, v1 = hi[0]
        return v0 + (v1 - v0) * (30 - d0) / (d1 - d0)
    d, v = min(points, key=lambda x: abs(x[0] - 30))
    return v if abs(d - 30) <= 10 else float("nan")


def _surface(chain: pd.DataFrame, session) -> tuple[float, float]:
    work = chain.copy()
    work["dte"] = (work["expiration"].dt.date - session).apply(lambda x: x.days)
    work = work[(work["dte"].between(7, 60)) & (work["iv"] > 0)]
    atm_points, skew_points = [], []
    for _exp, group in work.groupby("expiration"):
        dte = int(group["dte"].iloc[0])
        spot = float(group["underlying_price"].iloc[0])
        distance = (group["strike"] / spot).apply(math.log).abs()
        nearest = float(distance.min())
        atm = group[distance <= nearest + 1e-12]["iv"].median()
        if pd.notna(atm):
            atm_points.append((dte, float(atm)))
        calls = group[(group["type"] == "call") & group["delta"].notna()]
        puts = group[(group["type"] == "put") & group["delta"].notna()]
        if not calls.empty and not puts.empty:
            ci = (calls["delta"] - 0.25).abs().idxmin()
            pi = (puts["delta"] + 0.25).abs().idxmin()
            if abs(float(calls.loc[ci, "delta"]) - 0.25) <= 0.10 and abs(float(puts.loc[pi, "delta"]) + 0.25) <= 0.10:
                skew_points.append((dte, float(puts.loc[pi, "iv"] - calls.loc[ci, "iv"])))
    return _interp_30(atm_points), _interp_30(skew_points)


def chain_features(chain: pd.DataFrame, stable_lookup: dict[str, int] | None = None,
                   config: EngineConfig | None = None) -> dict:
    """One as-of-close chain row; contains no outcome or future field."""
    cfg = config or EngineConfig.default()
    session = chain["quote_ts"].iloc[0].tz_convert("UTC").date()
    oi = chain["open_interest"].astype("float64").fillna(0).to_numpy()
    iv = chain["iv"].astype("float64").fillna(0).to_numpy()
    T = years_to_expiry(chain, cfg.pricer.day_count)
    spot = chain["underlying_price"].astype("float64").to_numpy()
    gamma = bs_gamma(spot, chain["strike"].astype("float64"), T, iv,
                     cfg.pricer.risk_free_rate, cfg.pricer.dividend_yield)
    valid = (iv > 0) & (T > 0) & (oi > 0)
    denom = option_notional(chain)
    naive = float(contract_gex_recomputed(chain, config=cfg).sum())
    otm_cfg = replace(cfg, metrics=replace(cfg.metrics, dealer_sign_convention="otm_customer"))
    otm = float(contract_gex_recomputed(chain, config=otm_cfg).sum())
    emp = None; fallback = None
    if stable_lookup is not None:
        signs, is_fallback = empirical_signs(chain, stable_lookup)
        contrib = signs * gamma * oi * CONTRACT_SIZE * dollar_factor(spot, cfg.metrics.gex_convention)
        emp = float(contrib.sum())
        weights = np.abs(gamma * oi); total = float(weights.sum())
        fallback = float(weights[is_fallback].sum() / total) if total > 0 else None
    atm, skew = _surface(chain, session)
    bid = chain["bid"].astype("float64"); ask = chain["ask"].astype("float64")
    mid = (bid + ask) / 2
    qok = (bid >= 0) & (ask >= bid) & (mid > 0)
    rel_spread = ((ask - bid) / mid).where(qok)
    volume = chain["volume"].astype("float64").fillna(0).to_numpy()
    total_oi = float(oi.sum())
    return {
        "date": session.isoformat(), "symbol": str(chain["symbol"].iloc[0]),
        "gex_norm_bs_naive": naive / denom if denom > 0 else None,
        "gex_norm_bs_otm": otm / denom if denom > 0 else None,
        "gex_norm_bs_empirical": emp / denom if emp is not None and denom > 0 else None,
        "empirical_fallback_gamma_oi_fraction": fallback,
        "oi_iv_eligible_fraction": float(oi[valid].sum() / total_oi) if total_oi > 0 else None,
        "atm_iv_30d": None if not math.isfinite(atm) else atm,
        "put_call_skew_25d_30d": None if not math.isfinite(skew) else skew,
        "option_volume_notional": float(np.sum(volume * CONTRACT_SIZE * spot)),
        "quoted_relative_spread_median": (float(rel_spread.median())
                                           if rel_spread.notna().any() else None),
        "valid_quote_fraction": float(qok.mean()),
        "oi_expiring_7d_fraction": oi_expiring_within(chain, 7),
        "oi_expiring_30d_fraction": oi_expiring_within(chain, 30),
        "days_to_monthly_opex": days_to_monthly_opex(session),
        "month_end": int((pd.Timestamp(session) + pd.offsets.BDay(1)).month != session.month),
        "day_of_week": session.weekday(),
    }


def add_price_features(panel: pd.DataFrame, bars: pd.DataFrame,
                       market_close: pd.Series | None = None) -> pd.DataFrame:
    """Join same-day/trailing price controls only; never create a forward target."""
    out = panel.copy().sort_index()
    close = bars["close"].astype("float64").reindex(out.index)
    absret = (close / close.shift(1) - 1).abs()
    out["return_5d"] = close / close.shift(5) - 1
    out["return_20d"] = close / close.shift(20) - 1
    out["har_abs_daily"] = absret
    out["har_abs_weekly"] = absret.rolling(5).mean()
    out["har_abs_monthly"] = absret.rolling(22).mean()
    market = market_close.astype("float64") if market_close is not None else close
    out["lagged_market_return"] = (market / market.shift(1) - 1).reindex(out.index)
    out["log_option_volume_notional"] = np.log1p(out["option_volume_notional"])
    forbidden = [c for c in out if "target" in c.lower() or "forward" in c.lower() or "t_plus" in c.lower()]
    if forbidden:
        raise ValueError(f"outcome-like fields forbidden in feature panel: {forbidden}")
    return out


__all__ = ["chain_features", "add_price_features"]
