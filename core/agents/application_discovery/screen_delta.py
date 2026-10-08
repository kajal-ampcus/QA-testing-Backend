"""Compare a discovery screen with the previous map version."""

from __future__ import annotations

from typing import Any


def classify_screen(
    fingerprint: str, page_key: str, previous: list[dict[str, Any]]
) -> str:
    """Identity is the structural fingerprint, not how similar two images look."""
    previous_fingerprints = {str(item.get("fingerprint") or "") for item in previous}
    if fingerprint and fingerprint in previous_fingerprints:
        return "unchanged"
    previous_pages = {
        str(item.get("page_key") or item.get("functional_key") or item.get("url_pattern") or "")
        for item in previous
    }
    if page_key and page_key in previous_pages:
        return "changed"
    return "new"
