"""Read observed accessibility nodes without truncating embedded quotes."""

import json
import re
from contextlib import suppress
from typing import Any
from urllib.parse import urldefrag

_NODE = re.compile(
    r'uid=(\S+)\s+(\S+)\s+"(.*?)"(?=\s*(?:$|[A-Za-z][\w-]*='
    r"|(?:disabled|checked|selected|expanded|expandable|collapsed|required|focusable|focused|multiline|readonly)\b))"
)
_ATTRIBUTE = re.compile(r'\b(url|value|description)="((?:\\.|[^"\\])*)"')


def snapshot_text(snapshot: object) -> str:
    if isinstance(snapshot, list):
        return "\n".join(getattr(block, "text", "") for block in snapshot)
    return str(snapshot)


def parse_elements(snapshot: object) -> list[dict[str, Any]]:
    if isinstance(snapshot, dict):
        return [dict(node) for node in snapshot.get("elements", [])]
    nodes = []
    for line in snapshot_text(snapshot).splitlines():
        match = _NODE.search(line)
        if not match:
            continue
        uid, role, name = match.groups()
        with suppress(json.JSONDecodeError):
            name = json.loads('"' + name + '"')
        node: dict[str, Any] = {"uid": uid, "role": role, "name": name}
        tail = line[match.end() :]
        for key, value in _ATTRIBUTE.findall(tail):
            if key != "value" or role not in {"textbox", "searchbox"}:
                node[key] = value
        for flag in ("disabled", "checked", "selected", "expanded", "required", "readonly"):
            if re.search(rf"\b{flag}\b", tail):
                node[flag] = True
        nodes.append(node)
    return nodes


def merge_dom_hrefs(nodes: list[dict[str, Any]], extras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add same-origin <a href> targets that the accessibility snapshot omitted."""
    existing = {
        urldefrag(str(node.get("url") or ""))[0].rstrip("/")
        for node in nodes
        if node.get("url")
    }
    merged = list(nodes)
    for extra in extras:
        url = str(extra.get("url") or "")
        if not url:
            continue
        key = urldefrag(url)[0].rstrip("/")
        if not key or key in existing:
            continue
        existing.add(key)
        name = str(extra.get("name") or "").strip()
        merged.append({
            "role": "link",
            "name": name,
            "url": url,
        })
    return merged
