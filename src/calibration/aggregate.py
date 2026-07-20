"""Per-session aggregation: cached trades -> signed customer flow per bucket.

Bridges the raw trade cache (pull.py) and the pure map logic (classify/bucket/
signmap). For one session it:

  1. reads the session's SPOT (and per-contract gamma) from the stored canonical
     chain - spot for moneyness comes from the store, never the trade file;
  2. classifies every trade with the quote rule (classify.classify_vectorized);
  3. filters to the calibration window: moneyness within +/- 15% of spot (0.85 <=
     strike/spot <= 1.15) and DTE in [0, 60];
  4. buckets each trade (type x moneyness band x DTE band) and sums signed customer
     flow per bucket - both in contracts (primary) and gamma-weighted (cross-check),
     and both over ALL valid trades and over REGULAR-condition trades only (the
     robustness pass).

Needs the data stack (pandas / the parquet store); its tests are guarded like the
rest of the repo. The pure per-trade / per-bucket rules it calls are tested
stdlib-only in test_calibration_logic.py.
"""

from __future__ import annotations

import logging
from datetime import date

from . import classify as _classify
from .bucket import CALL, PUT, bucket_for

_log = logging.getLogger(__name__)

# Moneyness window (+/- 15% of spot) for the flow map - where the gamma is.
MONEYNESS_LO = 0.85
MONEYNESS_HI = 1.15
MAX_DTE = 60

# Trade-condition codes (OPRA, per the ThetaData Trade-Conditions reference). A
# multi-leg / spread / combo / floor print need not sit on THIS leg's own NBBO, so the
# quote rule is unreliable for it; the robustness pass EXCLUDES those and checks whether
# any bucket's sign moves. Single-leg electronic prints (0 REGULAR, 18 AUTO_EXECUTION,
# 95 INTERMARKET_SWEEP, 125 SINGLE_LEG_AUCTION) do sit on the NBBO and are kept.
# Excluded: 35 SPREAD, 36 STRADDLE, 37 BUY_WRITE, 38 COMBO, 129 SINGLE_LEG_FLOOR_TRADE,
# and the multi-leg family 130-139 (MULTI_LEG_* auto/auction/against-single-leg).
NONREGULAR_CONDITIONS = frozenset(
    {35, 36, 37, 38, 129} | set(range(130, 140)))

_RIGHT_TO_TYPE = {"CALL": CALL, "PUT": PUT, "C": CALL, "P": PUT}


def _coerce_session(value: "date | str") -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def load_spot_and_gamma(store_root: str, symbol: str, session: "date | str"):
    """(spot, gamma_lookup) from the stored chain, or (None, None) if not stored.

    spot = median underlying_price over the chain (matches the ThetaData adapter's
    own spot). gamma_lookup maps (expiration ISO date, strike, type) -> summed gamma
    (summed so multi-root index contracts collapse; for SPY it is one row per key).
    """
    from ..ingest import io as _io

    try:
        chain = _io.read_canonical(store_root, symbol.upper(), session)
    except (FileNotFoundError, OSError):
        return None, None
    if chain.empty:
        return None, None
    spot = float(chain["underlying_price"].astype("float64").median())

    g = chain[["expiration", "strike", "type", "gamma"]].copy()
    g["exp"] = g["expiration"].dt.strftime("%Y-%m-%d")
    g["strike"] = g["strike"].astype("float64")
    g["gamma"] = g["gamma"].astype("float64").fillna(0.0)
    lookup = g.groupby(["exp", "strike", "type"])["gamma"].sum().to_dict()
    return spot, lookup


def classify_session_trades(trades, spot: float, session: "date | str", *,
                            gamma_lookup: dict | None = None):
    """Classify + window-filter one session's trades. Returns a pandas DataFrame.

    Adds: ``type`` (call/put), ``moneyness`` (strike/spot), ``dte`` (calendar days),
    ``bucket``, ``customer_sign`` (+1/-1/0), ``category``, ``regular`` (condition in
    REGULAR_CONDITIONS), and ``gamma`` (from the lookup, 0 if absent). Rows outside the
    moneyness window, outside DTE [0, 60], with an unknown type, or with no bucket are
    dropped. An empty input yields an empty frame.
    """
    import numpy as np
    import pandas as pd

    sess = _coerce_session(session)
    if trades is None or len(trades) == 0:
        return pd.DataFrame()

    df = trades.copy()
    df["type"] = df["right"].astype("string").str.upper().map(_RIGHT_TO_TYPE)
    df = df[df["type"].isin((CALL, PUT))]
    if df.empty:
        return df

    strike = df["strike"].astype("float64")
    df["moneyness"] = strike / float(spot)
    exp = pd.to_datetime(df["expiration"]).dt.tz_localize(None).dt.date
    df["dte"] = exp.map(lambda e: (e - sess).days)

    in_window = (
        (df["moneyness"] >= MONEYNESS_LO) & (df["moneyness"] <= MONEYNESS_HI)
        & (df["dte"] >= 0) & (df["dte"] <= MAX_DTE)
    )
    df = df[in_window].copy()
    if df.empty:
        return df

    cats, signs = _classify.classify_vectorized(
        df["price"].to_numpy(), df["bid"].to_numpy(), df["ask"].to_numpy())
    df["category"] = cats
    df["customer_sign"] = signs.astype("int64")

    # Bucket per row (vectorized over the small set of distinct (type, mny, dte)).
    mb = np.where(df["moneyness"] <= 0.95, "<=0.95",
         np.where(df["moneyness"] <= 0.99, "0.95-0.99",
         np.where(df["moneyness"] <= 1.01, "0.99-1.01",
         np.where(df["moneyness"] <= 1.05, "1.01-1.05", ">1.05"))))
    db = np.where(df["dte"] <= 7, "0-7", np.where(df["dte"] <= 30, "8-30", "31-60"))
    df["bucket"] = df["type"].astype(str) + "|" + mb + "|" + db

    if "condition" in df.columns:
        df["regular"] = ~df["condition"].isin(NONREGULAR_CONDITIONS)
    else:
        df["regular"] = True

    if gamma_lookup:
        exp_iso = pd.to_datetime(df["expiration"]).dt.strftime("%Y-%m-%d")
        keys = list(zip(exp_iso, strike.loc[df.index], df["type"]))
        df["gamma"] = [float(gamma_lookup.get(k, 0.0)) for k in keys]
    else:
        df["gamma"] = 0.0
    return df


def aggregate_session(classified, session: "date | str") -> list[dict]:
    """Per-bucket signed customer flow records for one classified session.

    One record per bucket present: ``net_flow`` (sum of size*sign over valid non-mid
    trades, contracts), ``net_flow_reg`` (same, regular condition only), ``net_flow_gamma``
    (gamma-weighted), ``total_size`` (gross classified contracts), and buy/sell/mid/
    invalid trade counts. Suitable to feed signmap.build_sign_map.
    """
    sess = _coerce_session(session).isoformat()
    if classified is None or len(classified) == 0:
        return []

    df = classified
    directional = df[df["customer_sign"] != 0]
    signed_size = directional["size"].astype("float64") * directional["customer_sign"]
    directional = directional.assign(_signed_size=signed_size,
                                     _signed_gamma=signed_size * directional["gamma"])

    records: list[dict] = []
    for bucket, grp in df.groupby("bucket", sort=True):
        dgrp = grp[grp["customer_sign"] != 0]
        dgrp_reg = dgrp[dgrp["regular"]]
        net_flow = float((dgrp["size"].astype("float64") * dgrp["customer_sign"]).sum())
        net_flow_reg = float((dgrp_reg["size"].astype("float64") * dgrp_reg["customer_sign"]).sum())
        net_flow_gamma = float((dgrp["size"].astype("float64") * dgrp["customer_sign"]
                                * dgrp["gamma"]).sum())
        cats = grp["category"].value_counts()
        records.append({
            "session": sess,
            "bucket": bucket,
            "net_flow": net_flow,
            "net_flow_reg": net_flow_reg,
            "net_flow_gamma": net_flow_gamma,
            "total_size": float(dgrp["size"].astype("float64").sum()),
            "n_buys": int(cats.get(_classify.BUY, 0)),
            "n_sells": int(cats.get(_classify.SELL, 0)),
            "n_mid": int(cats.get(_classify.MID, 0)),
            "n_invalid": int(cats.get(_classify.INVALID, 0)),
        })
    return records


def session_stats(classified) -> dict:
    """Session-level data-quality summary (valid/mid/invalid shares, condition mix)."""
    if classified is None or len(classified) == 0:
        return {"n_trades": 0}
    df = classified
    n = int(len(df))
    cats = df["category"].value_counts()
    n_buy = int(cats.get(_classify.BUY, 0))
    n_sell = int(cats.get(_classify.SELL, 0))
    n_mid = int(cats.get(_classify.MID, 0))
    n_invalid = int(cats.get(_classify.INVALID, 0))
    n_valid = n - n_invalid
    out = {
        "n_trades": n,
        "n_valid": n_valid,
        "valid_frac": n_valid / n if n else float("nan"),
        "buy_frac": n_buy / n if n else float("nan"),
        "sell_frac": n_sell / n if n else float("nan"),
        "mid_frac": n_mid / n if n else float("nan"),
        "invalid_frac": n_invalid / n if n else float("nan"),
        "regular_frac": float(df["regular"].mean()),
        "volume": float(df["size"].astype("float64").sum()),
    }
    # midpoint share among regular vs non-regular (supports the robustness rationale)
    for label, mask in (("regular", df["regular"]), ("nonregular", ~df["regular"])):
        sub = df[mask]
        if len(sub):
            out[f"mid_frac_{label}"] = float((sub["category"] == _classify.MID).mean())
    return out


__all__ = [
    "MONEYNESS_LO", "MONEYNESS_HI", "MAX_DTE", "NONREGULAR_CONDITIONS",
    "load_spot_and_gamma", "classify_session_trades", "aggregate_session", "session_stats",
]
