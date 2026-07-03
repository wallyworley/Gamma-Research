"""Shared adapter helpers: numeric coercion, ET session timestamp, OI dating.

Extracted when the third adapter (Massive) landed - eodhd/cboe/massive all coerce
messy vendor scalars, anchor `quote_ts` to the ET session close, and stamp
`oi_asof_date` as a T-1 weekday. Defining these once keeps the OI-timing and
session conventions byte-identical across vendors (so they can't drift).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from math import exp, sqrt
from statistics import NormalDist
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

_ET = ZoneInfo("America/New_York")
_MARKET_CLOSE = (16, 0)
_NORM = NormalDist()


def num(value: Any) -> float | None:
    """Coerce to float; None/""/unparseable -> None. Zeros are kept as 0.0."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    f = num(value)
    return None if f is None else int(f)


def session_close_utc(session_date: date) -> datetime:
    """The ET session close (16:00 America/New_York) for ``session_date``, in UTC.

    Anchoring quote_ts to the close (rather than a raw feed clock) keeps its UTC
    *date* equal to the trading session and DST-correct across the year.
    """
    local = datetime(session_date.year, session_date.month, session_date.day,
                     _MARKET_CLOSE[0], _MARKET_CLOSE[1], tzinfo=_ET)
    return local.astimezone(timezone.utc)


def prior_weekday(session_date: date, lag: int = 1) -> date:
    """``session_date`` minus ``lag`` weekdays. Weekend-aware only, NOT holiday-aware
    (an unverified OI-timing assumption; review finding F1)."""
    if lag <= 0:
        return session_date
    return (pd.Timestamp(session_date) - pd.tseries.offsets.BusinessDay(lag)).date()


def et_date_from_epoch_ns(ns: Any) -> date | None:
    """The America/New_York calendar date of an epoch-**nanosecond** instant.

    Polygon option-snapshot ``day.last_updated`` fields are epoch ns; the ET date of
    the freshest one marks the trading session. Returns None for missing/unparseable.
    """
    n = num(ns)
    if n is None:
        return None
    return datetime.fromtimestamp(n / 1e9, tz=timezone.utc).astimezone(_ET).date()


def bs_implied_spot(strike: float | None, iv: float | None, delta: float | None,
                    tau: float | None, is_call: bool, r: float = 0.045) -> float | None:
    """Recover the underlying S implied by one option's Black-Scholes greeks.

    A vendor that publishes greeks has already solved d1 = N⁻¹(N(d1)) against *some*
    underlying; inverting it hands that S back, self-consistent with the gamma we
    integrate for GEX. With call delta = N(d1) and put delta = N(d1) − 1,

        S = K · exp( N⁻¹(N(d1))·IV·√τ − (r + ½·IV²)·τ ).

    Dividends are ignored (bias ≈ q·τ, sub-1% for near-ATM, short τ). Returns None when
    inputs are unusable (missing, non-positive iv/τ, or an implied N(d1) outside (0,1)).
    """
    if strike is None or iv is None or delta is None or tau is None:
        return None
    if strike <= 0 or iv <= 0 or tau <= 0:
        return None
    ncdf = delta if is_call else delta + 1.0
    if not (0.0 < ncdf < 1.0):
        return None
    d1 = _NORM.inv_cdf(ncdf)
    return strike * exp(d1 * iv * sqrt(tau) - (r + 0.5 * iv * iv) * tau)


__all__ = ["num", "to_int", "session_close_utc", "prior_weekday",
           "et_date_from_epoch_ns", "bs_implied_spot"]
