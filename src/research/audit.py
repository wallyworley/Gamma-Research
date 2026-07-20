"""Pre-outcome coverage, quality, outlier, and eligibility audit.

This module deliberately does not calculate forward returns or model scores.  It
freezes what data are eligible before the next experiment examines outcomes.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_SERIES_COLUMNS = (
    "spot", "net_gex", "net_gex_otm", "option_notional", "n_contracts"
)
REQUIRED_BAR_COLUMNS = ("open", "high", "low", "close")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _robust_outliers(values: pd.Series, threshold: float = 10.0) -> list[dict]:
    """Flag extreme values using median absolute deviation, without deleting them."""
    clean = values.replace([np.inf, -np.inf], np.nan).dropna().astype("float64")
    if clean.empty:
        return []
    median = float(clean.median())
    mad = float((clean - median).abs().median())
    if mad <= 0 or not math.isfinite(mad):
        return []
    robust_z = 0.6744897501960817 * (clean - median) / mad
    flagged = robust_z.abs() > threshold
    return [
        {"date": str(ts.date()), "value": float(clean.loc[ts]),
         "robust_z": float(robust_z.loc[ts])}
        for ts in clean.index[flagged]
    ]


def audit_series_and_bars(series: pd.DataFrame, bars: pd.DataFrame, *,
                          symbol: str, prospective_start: str,
                          minimum_history: int = 252) -> dict:
    missing_series = sorted(set(REQUIRED_SERIES_COLUMNS) - set(series.columns))
    missing_bars = sorted(set(REQUIRED_BAR_COLUMNS) - set(bars.columns))
    if missing_series or missing_bars:
        raise ValueError(f"missing columns: series={missing_series}, bars={missing_bars}")
    if not isinstance(series.index, pd.DatetimeIndex) or not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("series and bars must use DatetimeIndex")
    if series.index.has_duplicates or bars.index.has_duplicates:
        raise ValueError("duplicate session dates are not eligible")

    s = series.sort_index()
    b = bars.sort_index()
    overlap = s.index.intersection(b.index).sort_values()
    gex_norm = s["net_gex"].astype("float64") / s["option_notional"].astype("float64")
    gex_norm = gex_norm.where(s["option_notional"].astype("float64") > 0)
    bad_ohlc = (
        (b[list(REQUIRED_BAR_COLUMNS)] <= 0).any(axis=1)
        | (b["high"] < b[["open", "close", "low"]].max(axis=1))
        | (b["low"] > b[["open", "close", "high"]].min(axis=1))
    )
    bad_series = (
        (s["spot"].astype("float64") <= 0)
        | (s["option_notional"].astype("float64") <= 0)
        | (s["n_contracts"].astype("float64") <= 0)
    )
    prospective = overlap[overlap >= pd.Timestamp(prospective_start)]
    eligible = len(overlap) >= minimum_history and not overlap.empty

    return {
        "symbol": symbol.upper(),
        "series_sessions": int(len(s)),
        "bar_sessions": int(len(b)),
        "overlap_sessions": int(len(overlap)),
        "overlap_span": ([str(overlap.min().date()), str(overlap.max().date())]
                         if len(overlap) else None),
        "missing_series_sessions_within_bar_span": (
            [str(x.date()) for x in b.index[(b.index >= s.index.min()) &
                                             (b.index <= s.index.max())].difference(s.index)]
            if len(s) else []
        ),
        "invalid_series_dates": [str(x.date()) for x in s.index[bad_series]],
        "invalid_ohlc_dates": [str(x.date()) for x in b.index[bad_ohlc]],
        "null_counts": {c: int(s[c].isna().sum()) for c in REQUIRED_SERIES_COLUMNS},
        "spot_sources": ({str(k): int(v) for k, v in s["spot_source"].fillna("unknown").value_counts().items()}
                         if "spot_source" in s else {}),
        "outliers": {
            "gex_norm_mad10": _robust_outliers(gex_norm, 10.0),
            "option_notional_mad10": _robust_outliers(s["option_notional"], 10.0),
            "n_contracts_mad10": _robust_outliers(s["n_contracts"], 10.0),
        },
        "eligibility": {
            "minimum_history": int(minimum_history),
            "historical_development_eligible": bool(eligible),
            "historical_end": (str(overlap.max().date()) if len(overlap) else None),
            "prospective_holdout_start": prospective_start,
            "prospective_sessions_currently_available": int(len(prospective)),
            "holdout_status": "not_scored_by_this_pre_outcome_audit",
            "note": "Historical outcomes were already inspected; only dates on/after the prospective start can be untouched.",
        },
    }


__all__ = ["file_sha256", "audit_series_and_bars"]
