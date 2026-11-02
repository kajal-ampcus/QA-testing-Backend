"""Deterministic form data precedence, independent of storage and credentials."""

from copy import deepcopy
from typing import Any


def resolve_test_data(
    generated: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    assigned: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge objects recursively; explicit blanks, zero and false are values.

    Callers select a baseline by exact form context. Never pass vault data here.
    Provenance contains paths and source labels only.
    """
    result: dict[str, Any] = {}
    sources: dict[str, str] = {}

    def merge(target, incoming, source, prefix=""):
        for key, value in incoming.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                if not isinstance(target.get(key), dict):
                    target[key] = {}
                    sources.pop(path, None)
                merge(target[key], value, source, path)
            else:
                target[key] = deepcopy(value)
                for old in list(sources):
                    if old == path or old.startswith(path + "."):
                        del sources[old]
                sources[path] = source

    for data, source in ((generated, "generated"), (baseline, "baseline"),
                         (assigned, "assigned"), (overrides, "override")):
        merge(result, data or {}, source)
    return result, sources
