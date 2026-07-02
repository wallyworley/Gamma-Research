"""Canonical option-chain schema: the single source of truth for M1.

Every vendor adapter (EODHD, ORATS, Polygon, Databento, CBOE, Alpha Vantage)
must normalize its payload onto the fields defined here. The metric engine and
the parquet store both read this one contract, so locking it *before* any
vendor code is what makes the vendors actually swappable
(docs/phase_1_plan.md sections 5, 4.1, and guiding principle "Vendor-swappable data").

Design choices:
  * The schema is declared as plain Python data (CANONICAL_FIELDS). pandas and
    pyarrow types are *derived* from it lazily, so this module imports with only
    the standard library and can be inspected/validated with no data stack.
  * Nullability is explicit and encodes point-in-time safety. In particular
    `oi_asof_date` records which session open interest is as-of, so T-1 open
    interest can be aligned point-in-time instead of silently creating lookahead
    (docs/phase_1_plan.md sections 5 "OI timing caveat" and 8 "No-lookahead tests").
  * Value constraints (expiration >= quote date, oi_asof_date <= quote date,
    tz-aware quote_ts, positive strike/spot) are part of the contract, not the
    adapters. Enforcing them centrally means a whole class of lookahead and
    data-integrity bugs cannot reach the metric engine.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "1.0.0"

# Canonical option types. Stored lowercase; adapters must map vendor casings
# (C/P, CALL/PUT, ...) onto these.
OPTION_TYPES = ("call", "put")

# Natural key for one canonical row: one contract observed at one point in time.
PRIMARY_KEY = ("symbol", "quote_ts", "expiration", "strike", "type")


@dataclass(frozen=True)
class Field:
    """One canonical column.

    arrow / pandas hold *type tags* (strings), not live pyarrow/pandas objects,
    so this module stays import-light. arrow_schema() / pandas_dtypes() turn the
    tags into concrete types on demand.
    """

    name: str
    arrow: str          # one of: string, ts_utc, date, float64, int64
    pandas: str         # pandas (nullable) dtype string
    nullable: bool
    kind: str           # id | price | size | vol | greek | provenance
    doc: str


# Ordered canonical schema. Order is the on-disk/in-frame column order.
CANONICAL_FIELDS: tuple[Field, ...] = (
    # --- identity / point-in-time key -------------------------------------
    Field("symbol", "string", "string", False, "id", "underlying ticker"),
    Field("quote_ts", "ts_utc", "datetime64[ns, UTC]", False, "id",
          "point-in-time timestamp (tz-aware, UTC): EOD snapshot or intraday bar"),
    Field("expiration", "date", "datetime64[ns]", False, "id", "contract expiry (date)"),
    Field("strike", "float64", "Float64", False, "id", "strike price (> 0)"),
    Field("type", "string", "string", False, "id", "'call' or 'put'"),
    # --- prices -----------------------------------------------------------
    Field("underlying_price", "float64", "Float64", False, "price",
          "spot at quote_ts (> 0); adapter must attach it, GEX/DEX are undefined without it"),
    Field("bid", "float64", "Float64", True, "price", "contract bid (nullable)"),
    Field("ask", "float64", "Float64", True, "price", "contract ask (nullable)"),
    Field("last", "float64", "Float64", True, "price", "contract last (nullable)"),
    # --- size / open interest --------------------------------------------
    Field("open_interest", "int64", "Int64", True, "size",
          "contracts outstanding; typically T-1 for EOD sources"),
    Field("oi_asof_date", "date", "datetime64[ns]", True, "size",
          "session the open_interest is as-of; null => treat as T-1 of quote_ts. "
          "Must be <= quote_ts date (anti-lookahead)."),
    Field("volume", "int64", "Int64", True, "size", "contract volume (nullable)"),
    # --- vol / greeks -----------------------------------------------------
    Field("iv", "float64", "Float64", True, "vol", "implied vol; vendor or self-computed"),
    Field("delta", "float64", "Float64", True, "greek", "vendor or self-computed"),
    Field("gamma", "float64", "Float64", True, "greek", "vendor or self-computed"),
    Field("theta", "float64", "Float64", True, "greek", "vendor or self-computed"),
    Field("vega", "float64", "Float64", True, "greek", "vendor or self-computed"),
    Field("rho", "float64", "Float64", True, "greek", "vendor or self-computed"),
    # --- provenance (for cross-vendor comparison, M6) ---------------------
    Field("_iv_source", "string", "string", True, "provenance",
          "vendor name or pricer id that produced iv"),
    Field("_greek_source", "string", "string", True, "provenance",
          "vendor name or pricer id that produced the greeks"),
    Field("_adapter", "string", "string", False, "provenance",
          "adapter that produced this row (ChainAdapter.name)"),
)

_FIELDS_BY_NAME: dict[str, Field] = {f.name: f for f in CANONICAL_FIELDS}
REQUIRED_FIELDS: tuple[str, ...] = tuple(f.name for f in CANONICAL_FIELDS if not f.nullable)
NULLABLE_FIELDS: tuple[str, ...] = tuple(f.name for f in CANONICAL_FIELDS if f.nullable)


class SchemaError(ValueError):
    """Raised when data does not satisfy the canonical contract.

    Carries the full list of issues so an adapter author sees every problem at
    once rather than one-per-run.
    """

    def __init__(self, issues: Sequence[str]):
        self.issues = list(issues)
        preview = "\n  - ".join(self.issues[:20])
        more = "" if len(self.issues) <= 20 else f"\n  ... (+{len(self.issues) - 20} more)"
        super().__init__(f"{len(self.issues)} schema violation(s):\n  - {preview}{more}")


# --------------------------------------------------------------------------- #
# Introspection helpers (stdlib only)
# --------------------------------------------------------------------------- #

def field_names() -> list[str]:
    """Canonical column order."""
    return [f.name for f in CANONICAL_FIELDS]


def pandas_dtypes() -> dict[str, str]:
    """Column -> pandas (nullable) dtype string. No pandas import required."""
    return {f.name: f.pandas for f in CANONICAL_FIELDS}


def arrow_schema():
    """Return a pyarrow.Schema for the canonical layout (lazy import)."""
    import pyarrow as pa

    tag_to_arrow = {
        "string": pa.string(),
        "ts_utc": pa.timestamp("ns", tz="UTC"),
        "date": pa.date32(),
        "float64": pa.float64(),
        "int64": pa.int64(),
    }
    return pa.schema(
        [pa.field(f.name, tag_to_arrow[f.arrow], nullable=f.nullable) for f in CANONICAL_FIELDS],
        metadata={b"schema_version": SCHEMA_VERSION.encode()},
    )


def partition_relpath(symbol: str, quote_date: "_dt.date | _dt.datetime | str") -> str:
    """Hive-style partition directory: symbol=<SYM>/date=<YYYY-MM-DD>.

    Parquet is partitioned by symbol and date (docs/phase_1_plan.md section 4.1).
    """
    return f"symbol={symbol.upper()}/date={_coerce_date(quote_date).isoformat()}"


# --------------------------------------------------------------------------- #
# Validation (stdlib only): the enforceable half of the contract
# --------------------------------------------------------------------------- #

def _coerce_date(value: Any) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        return _dt.date.fromisoformat(value[:10])
    raise TypeError(f"cannot interpret {value!r} as a date")


def _is_number(value: Any) -> bool:
    # bool is a subclass of int but is never a valid numeric field value here.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _pk_key(row: Mapping[str, Any]) -> tuple:
    """Normalized primary-key tuple for one row (raises if a field is uncoercible)."""
    qts = row["quote_ts"]
    return (
        row["symbol"],
        qts.isoformat() if hasattr(qts, "isoformat") else str(qts),
        _coerce_date(row["expiration"]).isoformat(),
        float(row["strike"]),
        row["type"],
    )


def _is_tz_aware(value: Any) -> bool:
    if isinstance(value, _dt.datetime):
        return value.tzinfo is not None and value.utcoffset() is not None
    if isinstance(value, str):
        # crude but adequate: an ISO string carrying Z or a +hh:mm/-hh:mm offset.
        return value.endswith("Z") or value[10:].find("+") != -1 or value[11:].rfind("-") != -1
    return False


def validate_records(
    records: Iterable[Mapping[str, Any]],
    *,
    raise_on_error: bool = True,
) -> list[str]:
    """Validate canonical rows given as dict-like mappings (pure Python).

    This is the authoritative rule set; validate_frame() defers to it after
    converting a DataFrame to records. Returns the list of issues; raises
    SchemaError if raise_on_error and any issue is found.
    """
    issues: list[str] = []
    canonical = set(field_names())
    seen_pk: dict[tuple, int] = {}   # PRIMARY_KEY tuple -> first row index

    for i, row in enumerate(records):
        # unknown columns
        for extra in set(row) - canonical:
            issues.append(f"row {i}: unknown field '{extra}'")

        # required present + non-null
        for name in REQUIRED_FIELDS:
            if row.get(name) is None:
                issues.append(f"row {i}: required field '{name}' is missing/null")

        # per-field value checks (only when a value is present)
        def val(name: str) -> Any:
            return row.get(name)

        if val("type") is not None and val("type") not in OPTION_TYPES:
            issues.append(f"row {i}: type={val('type')!r} not in {OPTION_TYPES}")

        for name in ("strike", "underlying_price"):
            v = val(name)
            if v is not None and (not _is_number(v) or v <= 0):
                issues.append(f"row {i}: {name}={v!r} must be a positive number")

        for name in ("bid", "ask", "last", "iv", "open_interest", "volume"):
            v = val(name)
            if v is not None and (not _is_number(v) or v < 0):
                issues.append(f"row {i}: {name}={v!r} must be a non-negative number")

        for name in ("delta", "gamma", "theta", "vega", "rho"):
            v = val(name)
            if v is not None and not _is_number(v):
                issues.append(f"row {i}: {name}={v!r} must be numeric")

        # point-in-time integrity
        qts = val("quote_ts")
        if qts is not None and not _is_tz_aware(qts):
            issues.append(f"row {i}: quote_ts={qts!r} must be timezone-aware (UTC)")

        try:
            qdate = _coerce_date(qts) if qts is not None else None
        except (TypeError, ValueError):
            issues.append(f"row {i}: quote_ts={qts!r} is not a valid timestamp")
            qdate = None

        exp = val("expiration")
        if exp is not None and qdate is not None:
            try:
                if _coerce_date(exp) < qdate:
                    issues.append(
                        f"row {i}: expiration {exp!r} precedes quote date {qdate.isoformat()} "
                        "(expired contract / lookahead)")
            except (TypeError, ValueError):
                issues.append(f"row {i}: expiration={exp!r} is not a valid date")

        oi_asof = val("oi_asof_date")
        if oi_asof is not None and qdate is not None:
            try:
                if _coerce_date(oi_asof) > qdate:
                    issues.append(
                        f"row {i}: oi_asof_date {oi_asof!r} is after quote date "
                        f"{qdate.isoformat()} (open-interest lookahead)")
            except (TypeError, ValueError):
                issues.append(f"row {i}: oi_asof_date={oi_asof!r} is not a valid date")

        # primary-key uniqueness: one contract per snapshot. A duplicate silently
        # double-counts OI/GEX downstream, so reject it here.
        if all(row.get(f) is not None for f in PRIMARY_KEY):
            try:
                key = _pk_key(row)
            except (TypeError, ValueError):
                key = None
            if key is not None:
                if key in seen_pk:
                    issues.append(
                        f"row {i}: duplicate primary key {key} (first seen at row {seen_pk[key]})")
                else:
                    seen_pk[key] = i

    if issues and raise_on_error:
        raise SchemaError(issues)
    return issues


def validate_frame(frame, *, raise_on_error: bool = True) -> list[str]:
    """Validate a pandas DataFrame against the canonical contract.

    Checks column presence/order-agnostic membership, then defers row-value
    checks to validate_records(). Adapters return frames; ChainAdapter.load()
    calls this so no non-conforming frame reaches the metric engine.
    """
    import pandas as pd  # lazy: keeps the schema importable without pandas

    issues: list[str] = []
    have = list(frame.columns)
    missing = [c for c in field_names() if c not in have]
    if missing:
        issues.append(f"frame missing canonical columns: {missing}")
    extra = [c for c in have if c not in set(field_names())]
    if extra:
        issues.append(f"frame has non-canonical columns: {extra}")

    # Convert to records for the shared rule set. NaN/NaT/pd.NA -> None.
    subset = [c for c in field_names() if c in have]
    records = [
        {k: (None if pd.isna(v) else v) for k, v in rec.items()}
        for rec in frame[subset].to_dict(orient="records")
    ]
    issues.extend(validate_records(records, raise_on_error=False))

    if issues and raise_on_error:
        raise SchemaError(issues)
    return issues
