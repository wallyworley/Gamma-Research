"""Ingestion: raw vendor payloads -> one canonical, validated option-chain schema.

Public contract (import from here):
  * schema     - CANONICAL_FIELDS, validate_records/validate_frame, arrow_schema,
                 SCHEMA_VERSION, SchemaError, partition layout.
  * ChainAdapter + registry - the vendor-swappable adapter interface.
  * write_canonical / read_canonical - the partitioned parquet store.

The schema module is stdlib-only and can be imported and validated without
pandas/pyarrow. The parquet I/O and concrete adapters need the data stack
(see requirements.txt), installed in M0.
"""

from . import schema
from .adapter import (
    ChainAdapter,
    get_adapter,
    register_adapter,
    registered_adapters,
)
from .schema import (
    CANONICAL_FIELDS,
    OPTION_TYPES,
    PRIMARY_KEY,
    SCHEMA_VERSION,
    SchemaError,
    arrow_schema,
    field_names,
    pandas_dtypes,
    partition_relpath,
    validate_frame,
    validate_records,
)

__all__ = [
    "schema",
    "CANONICAL_FIELDS",
    "OPTION_TYPES",
    "PRIMARY_KEY",
    "SCHEMA_VERSION",
    "SchemaError",
    "arrow_schema",
    "field_names",
    "pandas_dtypes",
    "partition_relpath",
    "validate_frame",
    "validate_records",
    "ChainAdapter",
    "register_adapter",
    "get_adapter",
    "registered_adapters",
]
