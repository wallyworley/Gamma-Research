"""Read-only contract contribution audit for suspicious GEX sessions.

The output contains hashes and derived diagnostics only; it never rewrites a
chain.  GEX is recomputed under the long-call/short-put convention because that
is the series on which the Week-1 MAD diagnostic was calculated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _safe_float(value):
    value = float(value)
    return value if math.isfinite(value) else None


def _records(frame: pd.DataFrame, columns: list[str]) -> list[dict]:
    out = []
    for row in frame[columns].to_dict("records"):
        out.append({k: (_safe_float(v) if isinstance(v, (float, np.floating)) else
                        int(v) if isinstance(v, (int, np.integer)) else
                        str(v) if isinstance(v, (pd.Timestamp,)) else v)
                    for k, v in row.items()})
    return out


def audit_partition(path: str | Path, *, symbol: str, session: str,
                    top_n: int = 20) -> dict:
    path = Path(path)
    if not path.exists():
        return {"symbol": symbol, "session": session, "status": "missing",
                "path": str(path)}
    df = pq.ParquetFile(path).read().to_pandas()
    required = {"symbol", "expiration", "strike", "type", "underlying_price",
                "gamma", "open_interest"}
    missing = sorted(required - set(df.columns))
    if missing:
        return {"symbol": symbol, "session": session, "status": "invalid_schema",
                "path": str(path), "missing_columns": missing,
                "parquet_sha256": sha256_file(path)}

    for optional, default in (("root", symbol), ("iv", np.nan), ("volume", np.nan),
                              ("_greek_source", "unknown"), ("_spot_source", "unknown"),
                              ("oi_asof_date", None)):
        if optional not in df:
            df[optional] = default
    numeric = ["strike", "underlying_price", "gamma", "open_interest", "iv", "volume"]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    session_ts = pd.Timestamp(session)
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["dte"] = (df["expiration"] - session_ts).dt.days
    sign = np.where(df["type"].astype(str).str.lower() == "call", 1.0, -1.0)
    df["gex"] = (sign * df["gamma"].fillna(0) * df["open_interest"].fillna(0)
                 * 100.0 * df["underlying_price"].pow(2) * 0.01)
    df["abs_gex"] = df["gex"].abs()
    df["option_notional"] = (df["open_interest"].fillna(0) * 100.0
                              * df["underlying_price"])
    net = float(df["gex"].sum())
    notional = float(df["option_notional"].sum())
    abs_total = float(df["abs_gex"].sum())
    key = ["symbol", "root", "expiration", "strike", "type"]
    duplicate_rows = int(df.duplicated(key, keep=False).sum())

    top = df.nlargest(top_n, "abs_gex").copy()
    top["expiration"] = top["expiration"].dt.strftime("%Y-%m-%d")
    top_share = float(top["abs_gex"].sum() / abs_total) if abs_total > 0 else None

    grouped = (df.groupby(["root", "expiration", "type", "dte"], dropna=False)
               .agg(net_gex=("gex", "sum"), abs_gex=("abs_gex", "sum"),
                    open_interest=("open_interest", "sum"), contracts=("gex", "size"))
               .reset_index())
    grouped["abs_share"] = grouped["abs_gex"] / abs_total if abs_total > 0 else np.nan
    grouped = grouped.nlargest(min(top_n, len(grouped)), "abs_gex")
    grouped["expiration"] = grouped["expiration"].dt.strftime("%Y-%m-%d")

    gamma_valid = df["gamma"].notna() & np.isfinite(df["gamma"])
    oi_positive = df["open_interest"].fillna(0) > 0
    return {
        "symbol": symbol,
        "session": session,
        "status": "audited",
        "path": str(path),
        "parquet_sha256": sha256_file(path),
        "rows": int(len(df)),
        "duplicate_key_rows": duplicate_rows,
        "spot": {"min": _safe_float(df["underlying_price"].min()),
                 "max": _safe_float(df["underlying_price"].max()),
                 "sources": {str(k): int(v) for k, v in df["_spot_source"].fillna("unknown").value_counts().items()}},
        "greek_sources": {str(k): int(v) for k, v in df["_greek_source"].fillna("unknown").value_counts().items()},
        "oi_asof_dates": {str(k): int(v) for k, v in df["oi_asof_date"].fillna("unknown").value_counts().items()},
        "coverage": {
            "gamma_valid_fraction": float(gamma_valid.mean()),
            "positive_oi_fraction": float(oi_positive.mean()),
            "positive_oi_with_valid_gamma_fraction": (float(gamma_valid[oi_positive].mean())
                                                       if oi_positive.any() else None),
        },
        "recomputed": {"net_gex": net, "option_notional": notional,
                       "gex_norm": net / notional if notional > 0 else None,
                       "gross_abs_gex": abs_total,
                       "top_contracts_abs_share": top_share},
        "top_contracts": _records(top, ["root", "expiration", "dte", "strike", "type",
                                         "gamma", "open_interest", "iv", "volume", "gex", "abs_gex"]),
        "top_expiry_root_type_buckets": _records(
            grouped, ["root", "expiration", "dte", "type", "contracts",
                      "open_interest", "net_gex", "abs_gex", "abs_share"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--session", nargs=2, action="append", metavar=("SYMBOL", "YYYY-MM-DD"),
                    required=True)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    audits = []
    for symbol, session in args.session:
        path = Path(args.root, f"symbol={symbol.upper()}", f"date={session}", "chain.parquet")
        audits.append(audit_partition(path, symbol=symbol.upper(), session=session, top_n=args.top))
    payload = {"convention": "long_call_short_put", "formula":
               "sign(type)*gamma*open_interest*100*spot^2*0.01", "audits": audits}
    Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"out": args.out, "audited": sum(x["status"] == "audited" for x in audits),
                      "missing": sum(x["status"] == "missing" for x in audits)}, indent=2))


if __name__ == "__main__":
    main()
