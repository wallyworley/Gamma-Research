"""Shared NYSE trading-day calendar: one source of holiday truth.

Two layers needed the same closed-day knowledge and had drifted apart: capture.py
carried a hardcoded holiday set (to no-op the nightly run on a closed day), while the
adapters stamped ``oi_asof_date`` with a weekday-only (holiday-blind) lag. That split
is review finding F1: Monday 2026-07-06's capture stamped ``oi_asof = Friday 2026-07-03``,
which is the observed Independence Day holiday - OI is actually as-of Thursday 2026-07-02.
Centralizing the calendar here fixes both: capture.py and the adapters read the *same*
set, so the trading-day guard and the OI-dating logic can never disagree again.

Pure standard library (date arithmetic only), so it imports with no data stack - the
adapters can date open interest without pandas.

MAINTENANCE: the holiday set is hardcoded (no calendar dependency) and currently runs
**2025 through 2028**. Extend it yearly, or swap for the ``exchange_calendars`` package
if a dependency becomes acceptable. Dates are the NYSE full-day closures (weekday
closures only; weekends are handled by ``is_trading_day``). Observance rule applied:
a holiday on Saturday is observed the preceding Friday, on Sunday the following Monday -
**except New Year's Day, which the NYSE does NOT observe on the preceding Friday when
Jan 1 falls on a Saturday** (so 2028 has no New Year closure: Jan 1 2028 is a Saturday).
"""

from __future__ import annotations

from datetime import date, timedelta

# NYSE full-day market holidays, 2025-2028 (verified weekday observances).
# Half-days (e.g. the day after Thanksgiving, Christmas Eve early closes) are NOT here:
# the market is open those days, so they are trading days for capture/OI purposes.
_MARKET_HOLIDAYS = frozenset({
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    # 2026 (July 4 is a Saturday -> observed Fri 2026-07-03)
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027 (Juneteenth Sat -> Fri 06-18; July 4 Sun -> Mon 07-05; Christmas Sat -> Fri 12-24)
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
    # 2028 (no New Year: Jan 1 2028 is a Saturday, NOT observed on the preceding Friday)
    date(2028, 1, 17), date(2028, 2, 21), date(2028, 4, 14), date(2028, 5, 29),
    date(2028, 6, 19), date(2028, 7, 4), date(2028, 9, 4), date(2028, 11, 23),
    date(2028, 12, 25),
})


def is_trading_day(d: date) -> bool:
    """True if ``d`` is a NYSE session (a weekday and not a listed market holiday)."""
    return d.weekday() < 5 and d not in _MARKET_HOLIDAYS


def prior_trading_day(d: date, lag: int = 1) -> date:
    """The trading day ``lag`` sessions before ``d`` (weekend- AND holiday-aware).

    Steps back one calendar day at a time, counting only NYSE sessions, so a lag that
    would land on a weekend or a market holiday skips to the prior real session instead.
    ``lag <= 0`` returns ``d`` unchanged (an EOD adapter's ``oi_lag_days=0`` case).

    This is the holiday-aware replacement for the old weekday-only lag (F1): e.g.
    ``prior_trading_day(2026-07-06) == 2026-07-02`` (Monday back past the Fri 07-03
    observed July 4 holiday and the weekend), not the naive 2026-07-03.
    """
    if lag <= 0:
        return d
    result = d
    remaining = lag
    while remaining > 0:
        result -= timedelta(days=1)
        if is_trading_day(result):
            remaining -= 1
    return result


__all__ = ["is_trading_day", "prior_trading_day"]
