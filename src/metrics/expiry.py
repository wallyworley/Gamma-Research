"""Expiration-calendar features for the OpEx event study (quant review item 6).

Two pure, self-contained features paired with the vanna/charm exposures:

  * ``days_to_monthly_opex`` - calendar days from a session to the standard monthly
    equity-options expiration (the 3rd Friday of the month). This is the flow the
    vanna/charm hedging concentrates around.
  * ``oi_expiring_within`` - the fraction of a chain's open interest expiring within
    N calendar days of the snapshot session, a direct "how much is rolling off soon"
    gauge.

Known limitation (documented, not adjusted): monthly equity options nominally expire
on the 3rd Friday, but a holiday-shifted expiry (e.g. Good Friday) actually settles
the preceding Thursday. ``days_to_monthly_opex`` returns the nominal 3rd Friday and
does NOT apply that shift; callers needing settlement-exact dates near holidays must
adjust separately.
"""

from __future__ import annotations

import calendar
import datetime as dt

import pandas as pd

from ._common import require_single_snapshot


def third_friday(year: int, month: int) -> dt.date:
    """The 3rd Friday of ``year``/``month`` (nominal monthly OpEx date)."""
    first_weekday = calendar.monthrange(year, month)[0]  # Mon=0 .. Sun=6
    first_friday_day = 1 + (calendar.FRIDAY - first_weekday) % 7
    return dt.date(year, month, first_friday_day + 14)


def days_to_monthly_opex(session_date: dt.date) -> int:
    """Calendar days from ``session_date`` to the next monthly OpEx (3rd Friday).

    On OpEx day itself the answer is 0; strictly after this month's 3rd Friday it
    rolls to next month's. Nominal only - see the module holiday-shift limitation.
    """
    if isinstance(session_date, dt.datetime):
        session_date = session_date.date()
    this_month = third_friday(session_date.year, session_date.month)
    if session_date <= this_month:
        return (this_month - session_date).days
    year = session_date.year + (session_date.month == 12)
    month = 1 if session_date.month == 12 else session_date.month + 1
    return (third_friday(year, month) - session_date).days


def oi_expiring_within(df: pd.DataFrame, days: int) -> float:
    """Fraction of total open interest expiring within ``days`` calendar days.

    Measured from the snapshot's session date (the UTC calendar date of quote_ts,
    matching the rest of the metric engine). "Within" is inclusive of the boundary
    and of same-day (0DTE) expiries. Returns NaN when the chain carries no open
    interest (fraction undefined). Single-snapshot metric.
    """
    require_single_snapshot(df)
    if df.empty:
        return float("nan")
    oi = df["open_interest"].astype("float64").fillna(0.0)
    total = float(oi.sum())
    if total <= 0:
        return float("nan")
    session = df["quote_ts"].iloc[0].tz_convert("UTC").date()
    horizon = session + dt.timedelta(days=int(days))
    exp_date = df["expiration"].dt.date
    within = (exp_date >= session) & (exp_date <= horizon)
    return float(oi[within.to_numpy()].sum()) / total


__all__ = ["third_friday", "days_to_monthly_opex", "oi_expiring_within"]
