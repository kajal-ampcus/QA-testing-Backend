"""Helpers for discovery PNG files stored under DISCOVERY_EVIDENCE_DIR."""

from __future__ import annotations

import os
from pathlib import Path


def evidence_filename(evidence_ref: str | None) -> str | None:
    if not evidence_ref:
        return None
    name = evidence_ref.rsplit("/", 1)[-1]
    if not name.endswith(".png"):
        return None
    return name


def evidence_sha256(evidence_ref: str | None) -> str | None:
    name = evidence_filename(evidence_ref)
    if name is None:
        return None
    directory = Path(os.environ.get("DISCOVERY_EVIDENCE_DIR", "artifacts/discovery"))
    sidecar = directory / f"{name}.sha256"
    if not sidecar.is_file():
        return None
    digest = sidecar.read_text(encoding="utf-8").strip()
    return digest or None


def collect_evidence_names(payload: object) -> set[str]:
    """Pull discovery PNG names out of checkpoints, diagnostics, and execution evidence."""
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, str):
            name = evidence_filename(value)
            if name:
                found.add(name)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return found
