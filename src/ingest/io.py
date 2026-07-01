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

from .schema import arrow_schema, field_names, partition_relpath, validate_frame

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
    pq.write_table(table, path)
    return path


def read_canonical(root: str, symbol: str,
                   quote_date: "date | datetime | str") -> "pd.DataFrame":
    """Read one symbol/date partition back as a validated canonical DataFrame."""
    import pyarrow.parquet as pq

    path = os.path.join(root, partition_relpath(symbol, quote_date), _FILENAME)
    frame = pq.read_table(path).to_pandas(types_mapper=None)
    validate_frame(frame)
    return frame


__all__ = ["write_canonical", "read_canonical"]
