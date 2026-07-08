"""Bucket taxonomy for the flow map - PURE logic, stdlib-only import.

The calibration does NOT try to reconstruct per-contract dealer inventory (open vs
close is unobservable). Instead it aggregates signed customer flow into coarse,
economically meaningful buckets and reads a standing dealer-sign per bucket from the
persistent direction of opening flow. Because options expire, a bucket's flow
direction repeated across many sampled days is a defensible proxy for the dealer's
standing inventory sign in that region of the surface.

A bucket is  type (call/put)  x  moneyness band  x  DTE band:

  * moneyness m = strike / spot (spot from the session's stored chain, never the
    trade file). Bands: <=0.95, 0.95-0.99, 0.99-1.01, 1.01-1.05, >1.05 (open-ended
    ends cover every strike, so every contract lands in exactly one band).
  * DTE = calendar days from the session to expiration. Bands: 0-7, 8-30, 31-60.
    A contract with DTE > 60 (or < 0) has NO band (returns None) and is handled by
    the caller's fallback - the calibration window is DTE <= 60, where the gamma is.

These boundaries are fixed up front (documented here) and are NOT tuned against any
downstream scorecard result (anti-overfitting discipline).
"""

from __future__ import annotations

CALL = "call"
PUT = "put"

# Moneyness band edges (upper-inclusive), open-ended below the first and above the last.
MONEYNESS_BANDS = ("<=0.95", "0.95-0.99", "0.99-1.01", "1.01-1.05", ">1.05")
_MONEYNESS_EDGES = (0.95, 0.99, 1.01, 1.05)

# DTE band edges (upper-inclusive) within the DTE <= 60 calibration window.
DTE_BANDS = ("0-7", "8-30", "31-60")
_DTE_EDGES = (7, 30, 60)


def moneyness_band(strike: float, spot: float) -> str | None:
    """Band for m = strike / spot. None if spot/strike is non-positive/missing.

    Upper-inclusive: m <= 0.95 -> '<=0.95'; 0.95 < m <= 0.99 -> '0.95-0.99'; ...;
    m > 1.05 -> '>1.05'. The open-ended ends mean every valid strike has a band.
    """
    if strike is None or spot is None:
        return None
    try:
        strike = float(strike)
        spot = float(spot)
    except (TypeError, ValueError):
        return None
    if strike <= 0.0 or spot <= 0.0:
        return None
    m = strike / spot
    for edge, label in zip(_MONEYNESS_EDGES, MONEYNESS_BANDS[:-1]):
        if m <= edge:
            return label
    return MONEYNESS_BANDS[-1]


def dte_band(dte_days) -> str | None:
    """Band for calendar days-to-expiry. None outside the DTE <= 60 window (or < 0).

    Upper-inclusive within the window: dte <= 7 -> '0-7'; 7 < dte <= 30 -> '8-30';
    30 < dte <= 60 -> '31-60'; dte < 0 or dte > 60 -> None (caller falls back).
    """
    if dte_days is None:
        return None
    try:
        dte = int(dte_days)
    except (TypeError, ValueError):
        return None
    if dte < 0 or dte > _DTE_EDGES[-1]:
        return None
    for edge, label in zip(_DTE_EDGES, DTE_BANDS):
        if dte <= edge:
            return label
    return None  # unreachable (dte <= 60 handled above), kept for total-function safety


def bucket_key(opt_type: str, mny_band: str, dte_band_label: str) -> str:
    """Canonical bucket key ``type|moneyness|dte`` (e.g. ``call|0.99-1.01|0-7``)."""
    return f"{opt_type}|{mny_band}|{dte_band_label}"


def bucket_for(opt_type: str, strike: float, spot: float, dte_days) -> str | None:
    """Full bucket key for one contract, or None if it falls outside the window.

    None when the option type is unknown, the moneyness is undefined, or the DTE is
    outside 0-60 (the caller then uses the fallback dealer sign).
    """
    if opt_type not in (CALL, PUT):
        return None
    mb = moneyness_band(strike, spot)
    db = dte_band(dte_days)
    if mb is None or db is None:
        return None
    return bucket_key(opt_type, mb, db)


def parse_bucket_key(key: str) -> tuple[str, str, str]:
    """Inverse of ``bucket_key``: 'call|0.99-1.01|0-7' -> ('call','0.99-1.01','0-7')."""
    parts = key.split("|")
    if len(parts) != 3:
        raise ValueError(f"malformed bucket key {key!r}; expected 'type|moneyness|dte'")
    return parts[0], parts[1], parts[2]


def all_bucket_keys() -> list[str]:
    """Every possible bucket key (2 types x 5 moneyness x 3 DTE = 30), for reporting."""
    return [bucket_key(t, m, d)
            for t in (CALL, PUT) for m in MONEYNESS_BANDS for d in DTE_BANDS]


__all__ = [
    "CALL", "PUT", "MONEYNESS_BANDS", "DTE_BANDS",
    "moneyness_band", "dte_band", "bucket_key", "bucket_for",
    "parse_bucket_key", "all_bucket_keys",
]
