"""Research-governance helpers: locked manifests and pre-outcome data audits."""

from .audit import audit_series_and_bars
from .registry import canonical_hash, load_and_verify_manifest

__all__ = ["audit_series_and_bars", "canonical_hash", "load_and_verify_manifest"]
