"""Read observed accessibility nodes without truncating embedded quotes."""

import json
import re
from contextlib import suppress
from typing import Any

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
