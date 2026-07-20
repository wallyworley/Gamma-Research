"""Bucket-level dealer-sign map + per-contract fallback - PURE logic, stdlib-only.

Turns per-session, per-bucket signed customer flow into a standing dealer-sign map:

  dealer_sign(bucket) = MINUS sign( mean over sampled sessions of net customer flow )

with a t-stat across sessions for support and a two-halves stability check. A bucket
is USED downstream only if it is STABLE (both time halves agree in sign with enough
support); otherwise a contract in that bucket falls back to the naive
``long_call_short_put`` sign. The fallback share is reported so the empirical arm's
dependence on the assumption it is trying to replace is always visible.

Everything here is stdlib-only (``statistics``) so the classification/stability/
fallback logic is unit-tested in the stdlib CI leg without a data stack.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from .bucket import bucket_for

# Naive fallback dealer signs (mirrors metrics._common.DEALER_SIGNS["long_call_short_put"]
# without importing it, so this module stays stdlib-only).
_FALLBACK_SIGN = {"call": 1, "put": -1}

DEFAULT_SPLIT_DATE = "2022-01-01"   # early = sessions before this, late = on/after
DEFAULT_MIN_SESSIONS = 8            # min sampled sessions for a bucket to be usable
DEFAULT_MIN_SESSIONS_HALF = 4       # min sessions in EACH half for a stability verdict


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0


def _t_stat(xs: list[float]) -> float:
    """One-sample t of net flow vs 0 across sessions.

    NaN with < 2 observations; when every observation is identical (zero variance)
    the mean is either exactly 0 (t = 0) or a perfectly consistent non-zero flow,
    reported as +/- inf (maximally significant), so a tiny-but-perfectly-consistent
    bucket is not silently dropped.
    """
    n = len(xs)
    if n < 2:
        return float("nan")
    mean = statistics.fmean(xs)
    sd = statistics.stdev(xs)
    if sd == 0.0:
        return 0.0 if mean == 0.0 else (float("inf") if mean > 0 else float("-inf"))
    return mean / (sd / (n ** 0.5))


def build_sign_map(session_flows, *, split_date: str = DEFAULT_SPLIT_DATE,
                   min_sessions: int = DEFAULT_MIN_SESSIONS,
                   min_sessions_half: int = DEFAULT_MIN_SESSIONS_HALF) -> dict:
    """Build the dealer-sign map from per-session per-bucket customer flow records.

    ``session_flows``: iterable of mappings with at least ``session`` (ISO date str),
    ``bucket`` (key from bucket.py), and ``net_flow`` (signed customer contracts for
    that session+bucket: buys minus sells). Optional ``total_size`` (gross contracts)
    is summed into per-bucket support.

    Returns ``{bucket: stats}`` where stats has: ``dealer_sign`` (-1/0/+1),
    ``customer_mean_flow``, ``t_stat``, ``n_sessions``, ``total_contracts``,
    ``early_sign``/``late_sign``/``early_n``/``late_n``, and ``stable`` (bool). A bucket
    is stable when it has >= ``min_sessions`` sampled sessions, >= ``min_sessions_half``
    in EACH time half, and the early-half, late-half, and overall mean-flow signs all
    agree and are non-zero.
    """
    per_bucket_all: dict[str, list[float]] = defaultdict(list)
    per_bucket_early: dict[str, list[float]] = defaultdict(list)
    per_bucket_late: dict[str, list[float]] = defaultdict(list)
    per_bucket_size: dict[str, float] = defaultdict(float)

    for rec in session_flows:
        bucket = rec["bucket"]
        flow = float(rec["net_flow"])
        session = str(rec["session"])[:10]
        per_bucket_all[bucket].append(flow)
        (per_bucket_early if session < split_date else per_bucket_late)[bucket].append(flow)
        per_bucket_size[bucket] += float(rec.get("total_size", 0.0) or 0.0)

    out: dict[str, dict] = {}
    for bucket, flows in per_bucket_all.items():
        early = per_bucket_early.get(bucket, [])
        late = per_bucket_late.get(bucket, [])
        mean_all = _mean(flows)
        early_mean, late_mean = _mean(early), _mean(late)
        e_sign, l_sign, o_sign = _sign(early_mean), _sign(late_mean), _sign(mean_all)

        stable = (
            len(flows) >= min_sessions
            and len(early) >= min_sessions_half
            and len(late) >= min_sessions_half
            and e_sign != 0 and l_sign != 0
            and e_sign == l_sign == o_sign
        )
        out[bucket] = {
            "dealer_sign": -o_sign,                    # dealer = minus customer flow sign
            "customer_mean_flow": mean_all,
            "t_stat": _t_stat(flows),
            "n_sessions": len(flows),
            "total_contracts": per_bucket_size[bucket],
            "early_n": len(early),
            "late_n": len(late),
            "early_sign": e_sign,
            "late_sign": l_sign,
            "early_mean_flow": early_mean,
            "late_mean_flow": late_mean,
            "stable": stable,
        }
    return out


def stable_sign_lookup(sign_map: dict) -> dict[str, int]:
    """{bucket: dealer_sign} for STABLE buckets with a non-zero dealer sign only."""
    return {b: s["dealer_sign"] for b, s in sign_map.items()
            if s.get("stable") and s.get("dealer_sign", 0) != 0}


def fallback_sign(opt_type: str) -> int:
    """Naive long_call_short_put dealer sign (call +1, put -1; 0 for unknown type)."""
    return _FALLBACK_SIGN.get(opt_type, 0)


def empirical_contract_sign(opt_type: str, strike: float, spot: float, dte_days,
                            stable_lookup: dict[str, int]) -> tuple[int, str]:
    """Dealer sign for ONE contract under the empirical map, with fallback.

    Returns ``(sign, source)`` where source is ``"empirical"`` when the contract's
    bucket is stable (and in ``stable_lookup``) and ``"fallback"`` otherwise (DTE > 60,
    undefined moneyness, or an unstable/insufficient bucket -> naive sign). Pass the
    precomputed ``stable_lookup`` (from ``stable_sign_lookup``) so a whole chain can be
    signed without rebuilding the map per row.
    """
    bucket = bucket_for(opt_type, strike, spot, dte_days)
    if bucket is not None and bucket in stable_lookup:
        return stable_lookup[bucket], "empirical"
    return fallback_sign(opt_type), "fallback"


__all__ = [
    "DEFAULT_SPLIT_DATE", "DEFAULT_MIN_SESSIONS", "DEFAULT_MIN_SESSIONS_HALF",
    "build_sign_map", "stable_sign_lookup", "fallback_sign", "empirical_contract_sign",
]
