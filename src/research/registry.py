"""Immutable experiment-manifest primitives.

The manifest's ``manifest_hash`` is the SHA-256 of its canonical JSON content with
that field removed.  Editing any research choice therefore invalidates the lock;
a changed design must be registered as a new experiment rather than silently
overwriting the old one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


REQUIRED_TOP_LEVEL = {
    "experiment_id", "status", "registered_at", "question", "hypothesis",
    "universe", "signal", "target", "controls", "validation", "pass_fail",
    "placebos", "exclusions", "manifest_hash",
}


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical bytes used by the lock, excluding the lock field itself."""
    body = {k: v for k, v in payload.items() if k != "manifest_hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def validate_manifest(payload: dict[str, Any]) -> list[str]:
    issues = []
    missing = sorted(REQUIRED_TOP_LEVEL - set(payload))
    if missing:
        issues.append(f"missing required fields: {', '.join(missing)}")
    if payload.get("status") != "locked":
        issues.append("status must be 'locked'")
    expected = canonical_hash(payload)
    if payload.get("manifest_hash") != expected:
        issues.append(
            f"manifest_hash mismatch: stored={payload.get('manifest_hash')!r}, expected={expected}"
        )
    return issues


def load_and_verify_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("experiment manifest must be a JSON object")
    issues = validate_manifest(payload)
    if issues:
        raise ValueError("invalid experiment manifest: " + "; ".join(issues))
    return payload


__all__ = ["canonical_bytes", "canonical_hash", "validate_manifest",
           "load_and_verify_manifest"]
