"""Login choices derived from observed controls, without application role lists."""

import hashlib
import re
from urllib.parse import urljoin, urlsplit

_LOGIN = re.compile(r"\b(log\s*in|sign\s*in|login|signin)\b", re.I)
_EXCLUDE = re.compile(r"forgot|reset|recover|register|sign\s*up|show|hide|refresh|remember|captcha|submit|cancel", re.I)


def login_group_key(url: str, label: str) -> str:
    parsed = urlsplit(url)
    identity = f"{parsed.scheme}://{parsed.netloc}{parsed.path}|{label.strip().casefold()}"
    return "login-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def login_choices(nodes: list[dict], page_url: str) -> list[dict]:
    """Offer semantic switches, grouped buttons, and explicit login links.

    Group metadata comes from the live DOM. Unrelated navigation buttons are
    not inferred to be account roles merely because a password field exists.
    """
    has_password = any(n.get("input_type") == "password" or (
        n.get("role") in {"textbox", "input"} and re.search("password", n.get("name", ""), re.I)
    ) for n in nodes)
    login_context = has_password or bool(_LOGIN.search(page_url)) or any(
        n.get("role") in {"heading", "RootWebArea"} and _LOGIN.search(str(n.get("name") or "")) for n in nodes)
    groups: dict[str, list[dict]] = {}
    candidates = []
    for node in nodes:
        name = str(node.get("name") or "").strip()
        role = node.get("role")
        if not name or node.get("disabled") or node.get("visible") is False or _EXCLUDE.search(name):
            continue
        if role in {"link", "button"} and _LOGIN.search(name) and node.get("url"):
            candidates.append(node)
        elif login_context and role in {"tab", "radio"}:
            candidates.append(node)
        elif login_context and role == "button" and not _LOGIN.search(name):
            group = node.get("control_group")
            if group:
                groups.setdefault(group, []).append(node)
        elif login_context and role == "combobox" and node.get("options"):
            for option in node["options"]:
                candidates.append({**node, "name": name, "choice_label": option, "choice_value": option})
    for group in groups.values():
        if len(group) >= 2:
            candidates.extend(group)
    result = []
    seen = set()
    for node in candidates:
        label = str(node.get("choice_label") or node["name"])
        destination = urljoin(page_url, node["url"]) if node.get("url") else None
        if destination and urlsplit(destination).netloc != urlsplit(page_url).netloc:
            continue
        key = login_group_key(destination or page_url, label)
        if key in seen:
            continue
        seen.add(key)
        step = {"role": node["role"], "name": node["name"]}
        if destination:
            step["url"] = destination
        if node.get("choice_value"):
            step["value"] = node["choice_value"]
        result.append({"id": key, "label": label, "step": step})
    return result
