"""Identify repeated site navigation without dropping page-specific controls."""

import re
from urllib.parse import urljoin


def is_custom_navigation(element):
    return (element.get("role") == "StaticText"
            and element.get("navigation_clickable") is True
            and bool(str(element.get("name") or "").strip()))


def navigation_identity(element, current_url):
    role = element.get("role")
    if role not in {"link", "button", "menuitem", "tab"} and not is_custom_navigation(element):
        return None
    scope = str(element.get("locator_scope") or element.get("landmark") or "").lower()
    shared = is_custom_navigation(element) or bool(re.search(r"\b(header|footer|nav|banner|contentinfo|navigation)\b", scope))
    destination = element.get("url")
    # Old snapshots have no landmarks. Exact route links can still be
    # recognized, but never deduplicate an unscoped page button by its label.
    if not shared and (scope or not destination or role != "link"):
        return None
    if destination:
        return "route:" + urljoin(current_url, destination)
    name = " ".join(str(element.get("name") or "").casefold().split())
    return f"control:{role}:{name}" if name else None


def inherited_navigation(catalog, path, skip_auth):
    inherited = set()
    for entry in catalog:
        parent = entry["path"]
        if entry["skip_auth"] == skip_auth and len(parent) < len(path) and path[:len(parent)] == parent:
            inherited.update(entry["actions"])
    return inherited
