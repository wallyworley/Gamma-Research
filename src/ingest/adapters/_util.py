"""Shared adapter helpers: numeric coercion, ET session timestamp, OI dating.

Extracted when the third adapter (Massive) landed - eodhd/cboe/massive all coerce
messy vendor scalars, anchor `quote_ts` to the ET session close, and stamp
`oi_asof_date` as a T-1 weekday. Defining these once keeps the OI-timing and
session conventions byte-identical across vendors (so they can't drift).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

_ET = ZoneInfo("America/New_York")
_MARKET_CLOSE = (16, 0)


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


__all__ = ["num", "to_int", "session_close_utc", "prior_weekday"]
