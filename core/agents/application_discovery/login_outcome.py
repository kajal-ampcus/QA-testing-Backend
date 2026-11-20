"""Resolve a successful login only from an unambiguous discovered transition."""

import re
from typing import Any


def observed_login_outcome(start: dict[str, Any], states: list[dict[str, Any]], expected: str, credential_ref: str | None = None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    prefix = start.get("reached_via") or []
    destinations = []
    for state in states:
        path = state.get("reached_via") or []
        if (state.get("state_code") != start.get("state_code") and len(path) == len(prefix) + 1
                and path[:len(prefix)] == prefix and re.search(r"role=authentication(?:,|\))", str(path[-1]))):
            destinations.append(state)
    if len(destinations) != 1:
        return None
    destination = destinations[0]
    account = str(destination.get("account_context") or "")
    if credential_ref and account.startswith("authenticated::") and account != f"authenticated::{credential_ref}":
        return None
    # Only a specific page name explicitly required by the case can be used
    # as the success oracle. A URL alone cannot distinguish SPA login states.
    page = re.search(r"\b(dashboard|home\s*page)\b", expected, re.I)
    if not page:
        return None
    name = " ".join(page.group(1).lower().split())
    headings = [element for element in destination.get("elements", [])
                if element.get("role") == "heading" and element.get("visible") is not False
                and " ".join(str(element.get("name") or "").lower().split()) == name]
    if len(headings) != 1 or not headings[0].get("element_code"):
        return None
    if any(item.get("role") == "heading" and str(item.get("name") or "").strip().casefold() == name
           for item in start.get("elements", [])):
        return None
    return destination, headings[0]
