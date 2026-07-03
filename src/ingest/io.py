"""Canonical parquet store: on-disk half of the M1 contract.

Normalized chains are written as parquet partitioned by ``symbol`` and ``date``
(docs/phase_1_plan.md section 4.1), one directory per symbol/day:

    <root>/symbol=<SYM>/date=<YYYY-MM-DD>/chain.parquet

Both functions validate against the canonical schema at the boundary, so the
store can only ever contain conforming data. First exercised in M1, once the
data stack (pyarrow/pandas) is installed; the layout itself is fixed here.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import TYPE_CHECKING

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


__all__ = ["write_canonical", "read_canonical"]
