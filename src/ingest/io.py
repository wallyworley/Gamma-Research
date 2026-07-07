"""Canonical parquet store: on-disk half of the M1 contract.

Normalized chains are written as parquet partitioned by ``symbol`` and ``date``
(docs/phase_1_plan.md section 4.1), one directory per symbol/day:

    <root>/symbol=<SYM>/date=<YYYY-MM-DD>/chain.parquet

Both functions validate against the canonical schema at the boundary, so the
store can only ever contain conforming data. First exercised in M1, once the
data stack (pyarrow/pandas) is installed; the layout itself is fixed here.
"""

from __future__ import annotations

import glob
import os
from datetime import date, datetime
from typing import TYPE_CHECKING, Iterator

from .schema import (
    NULLABLE_FIELDS,
    arrow_schema,
    field_names,
    pandas_dtypes,
    partition_relpath,
    validate_frame,
)

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

_FILENAME = "chain.parquet"


def _as_date(value: "date | datetime | str") -> date:
    """Coerce a date / datetime / ISO string to a plain ``date`` (for range filters)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError(f"cannot interpret {value!r} as a date")


def write_canonical(frame: "pd.DataFrame", root: str, symbol: str,
                    quote_date: "date | datetime | str") -> str:
    """Validate ``frame`` and write it to the symbol/date partition. Returns path."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    validate_frame(frame)  # never persist non-conforming data
    ordered = frame[[c for c in field_names() if c in frame.columns]]
    table = pa.Table.from_pandas(ordered, schema=arrow_schema(), preserve_index=False)

    part_dir = os.path.join(root, partition_relpath(symbol, quote_date))
    os.makedirs(part_dir, exist_ok=True)
    path = os.path.join(part_dir, _FILENAME)
    # Write to a sibling temp then atomically rename: a SIGTERM/OOM/reboot mid-write,
    # or a re-capture overwriting an existing partition, can never leave a truncated
    # chain.parquet in the permanent store (os.replace is atomic on the same fs).
    tmp = os.path.join(part_dir, f".{_FILENAME}.{os.getpid()}.tmp")
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def read_canonical(root: str, symbol: str,
                   quote_date: "date | datetime | str") -> "pd.DataFrame":
    """Read one symbol/date partition back as a validated canonical DataFrame.

    Reads the single file directly (ParquetFile) rather than pq.read_table on the
    path: the latter would infer Hive partitioning from the ``symbol=.../date=...``
    directories and synthesize a dictionary-typed ``symbol`` partition column that
    collides with the real ``symbol`` string column kept inside the file. Callers
    already pass (symbol, quote_date), so no reconstruction from the path is needed.
    """
    import pandas as pd
    import pyarrow.parquet as pq

    path = os.path.join(root, partition_relpath(symbol, quote_date), _FILENAME)
    frame = pq.ParquetFile(path).read().to_pandas()
    # Schema evolution: a partition written before a nullable column was added lacks it.
    # Backfill any missing nullable canonical column as null so old data still validates.
    for name in NULLABLE_FIELDS:
        if name not in frame.columns:
            frame[name] = pd.Series([pd.NA] * len(frame), dtype=pandas_dtypes()[name])
    # `root` (added to the required key later) is absent on legacy partitions. Equity
    # captures have root == ticker, and the only pre-`root` index captures were collision-
    # free on the old key, so deriving root from `symbol` keeps them readable and unique.
    # (A rare secondary index root, e.g. a small DJXW tail under DJX, would be labeled DJX -
    # a label-only inaccuracy; OI and GEX are unaffected since the metric sums all rows.)
    if "root" not in frame.columns and "symbol" in frame.columns:
        frame["root"] = frame["symbol"]
    # Return canonical column order + dtypes. Parquet date32 otherwise reads back as
    # object (datetime.date), which breaks the metric engine's `.dt` horizon math; casting
    # here makes stored data behave exactly like an adapter's in-memory frame.
    frame = frame[field_names()].astype(pandas_dtypes())
    validate_frame(frame)
    return frame


def iter_partitions(root: str, symbol: str | None = None,
                    start: "date | datetime | str | None" = None,
                    end: "date | datetime | str | None" = None) -> "Iterator[tuple[str, date, str]]":
    """Yield ``(symbol, date, path)`` for each stored chain partition matching the filters.

    Globs the ``symbol=<SYM>/date=<YYYY-MM-DD>`` layout under ``root``. ``symbol`` (if
    given) restricts to one ticker; ``start``/``end`` (inclusive, date/datetime/ISO str)
    restrict the session date. Results are ordered by (symbol, date) so a caller reading
    a symbol's history walks its sessions in time order. No data stack needed - this only
    inspects the directory tree, so a scan can plan before any parquet is read.
    """
    sym_glob = f"symbol={symbol.upper()}" if symbol is not None else "symbol=*"
    pattern = os.path.join(root, sym_glob, "date=*", _FILENAME)
    lo = _as_date(start) if start is not None else None
    hi = _as_date(end) if end is not None else None

    found: list[tuple[str, date, str]] = []
    for path in glob.glob(pattern):
        sym: str | None = None
        d: date | None = None
        for part in os.path.relpath(path, root).split(os.sep):
            if part.startswith("symbol="):
                sym = part[len("symbol="):]
            elif part.startswith("date="):
                try:
                    d = date.fromisoformat(part[len("date="):])
                except ValueError:
                    d = None
        if sym is None or d is None:
            continue
        if lo is not None and d < lo:
            continue
        if hi is not None and d > hi:
            continue
        found.append((sym, d, path))
    yield from sorted(found, key=lambda t: (t[0], t[1]))


def read_symbol_history(root: str, symbol: str,
                        start: "date | datetime | str | None" = None,
                        end: "date | datetime | str | None" = None) -> "pd.DataFrame":
    """Read every stored session for ``symbol`` in [start, end] as one canonical frame.

    Concatenates ``read_canonical`` per matching partition (so every schema-evolution
    backfill - nullable columns, legacy ``root`` - applies to each), sorted by
    ``quote_ts``. Each partition is validated on read, so the result is canonical by
    construction. Returns an empty, canonically-typed frame when no partition matches.
    This is the time-series access path the metric / eval layers consume.
    """
    import pandas as pd

    frames = [read_canonical(root, sym, d)
              for sym, d, _ in iter_partitions(root, symbol=symbol, start=start, end=end)]
    if not frames:
        empty = {name: pd.Series([], dtype=pandas_dtypes()[name]) for name in field_names()}
        return pd.DataFrame(empty)[field_names()]
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values("quote_ts", kind="stable").reset_index(drop=True)


__all__ = ["write_canonical", "read_canonical", "iter_partitions", "read_symbol_history"]
