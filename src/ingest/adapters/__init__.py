"""Concrete vendor adapters. Importing a module registers it via @register_adapter.

These require the data stack (pandas), unlike src.ingest.schema which is
stdlib-only. Import this package (or a specific adapter) to make it selectable
through ingest.get_adapter(name).
"""

from .eodhd import EodhdAdapter

__all__ = ["EodhdAdapter"]
