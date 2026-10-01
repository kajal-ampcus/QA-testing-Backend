"""
Selector priority for observed application-map elements only.

1. getByRole(role, { name }) when a valid role and accessible name exist
2. getByLabel, then getByPlaceholder, when those were observed
3. getByTestId when observed
4. a stable observed CSS selector or id
5. XPath only as a last fallback

Nothing is invented. element_code is resolved inside one state, never across
the map. Transient browser or MCP ids are not locators.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_PLAYWRIGHT_ROLES = frozenset(
    {
        "alert",
        "alertdialog",
        "button",
        "checkbox",
        "combobox",
        "dialog",
        "heading",
        "img",
        "link",
        "listbox",
        "menu",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
        "treeitem",
    }
)

_ROLE_ALIASES = {"image": "img"}

_UNSTABLE_ID = re.compile(
    r"""(?x)
    ^(?:
        [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}
        | \d+
        | :r[0-9a-z]+:
        | uid[-_].+
        | ember\d+
        | react-select-.+
        | radix-.+
        | headlessui-.+
    )$
    """,
    re.IGNORECASE,
)

_LABEL_KEYS = ("label", "aria_label", "accessible_label")
_PLACEHOLDER_KEYS = ("placeholder", "placeholder_text")
_TESTID_KEYS = ("data-testid", "data_testid", "test_id", "testid")
_CSS_KEYS = ("css", "css_selector", "selector")
_XPATH_KEYS = ("xpath",)


@dataclass
class SelectorDecision:
    status: str
    strategy: str | None = None
    expression: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    xpath_fallback: bool = False
    risk: str = "SAFE"
    state_code: str = ""
    element_code: str | None = None


def resolve_in_state(
    state_code: str,
    element_code: str | None,
    elements: list[dict[str, Any]],
) -> SelectorDecision:
    """Resolve one element inside the state that owns it."""
    if not element_code:
        return SelectorDecision(
            status="blocked",
            reason=f"{state_code} step has no element_code.",
            state_code=state_code,
        )
    matches = [element for element in elements if element.get("element_code") == element_code]
    if not matches:
        return SelectorDecision(
            status="blocked",
            reason=f"{state_code}/{element_code} was not observed on this application map.",
            state_code=state_code,
            element_code=element_code,
        )
    if len(matches) > 1:
        return SelectorDecision(
            status="blocked",
            reason=(
                f"{state_code}/{element_code} matches {len(matches)} observed elements "
                "and is ambiguous."
            ),
            state_code=state_code,
            element_code=element_code,
            risk=_risk(matches[0]),
        )
    return _choose(state_code, matches[0], elements)


def _choose(
    state_code: str, element: dict[str, Any], siblings: list[dict[str, Any]]
) -> SelectorDecision:
    code = str(element.get("element_code") or "")
    risk = _risk(element)
    role = _role(element)
    name = _text(element.get("name")) or _locator_name(element)
    if role and name:
        same = [
            item
            for item in siblings
            if _role(item) == role and (_text(item.get("name")) or _locator_name(item)) == name
        ]
        if len(same) != 1:
            return _blocked(
                state_code,
                code,
                risk,
                f"{state_code}/{code} role {role!r} name {name!r} matches {len(same)} elements.",
            )
        return _resolved(
            state_code,
            element,
            "getByRole",
            f"getByRole({json.dumps(role)}, {{ name: {json.dumps(name)}, exact: true }})",
            {"role": role, "name": name},
        )

    label = _first(element, _LABEL_KEYS)
    if label:
        return _unique_text(
            state_code,
            element,
            siblings,
            "getByLabel",
            label,
            _LABEL_KEYS,
            f"getByLabel({json.dumps(label)}, {{ exact: true }})",
        )
    placeholder = _first(element, _PLACEHOLDER_KEYS)
    if placeholder:
        return _unique_text(
            state_code,
            element,
            siblings,
            "getByPlaceholder",
            placeholder,
            _PLACEHOLDER_KEYS,
            f"getByPlaceholder({json.dumps(placeholder)}, {{ exact: true }})",
        )
    test_id = _first(element, _TESTID_KEYS)
    if test_id:
        return _unique_text(
            state_code,
            element,
            siblings,
            "getByTestId",
            test_id,
            _TESTID_KEYS,
            f"getByTestId({json.dumps(test_id)})",
        )

    css = _css(element)
    if css:
        same = [item for item in siblings if _css(item) == css]
        if len(same) != 1:
            return _blocked(
                state_code,
                code,
                risk,
                f"{state_code}/{code} observed CSS selector {css!r} is ambiguous.",
            )
        return _resolved(
            state_code,
            element,
            "css",
            f"locator({json.dumps(css)})",
            {"css": css},
        )

    xpath = _first(element, _XPATH_KEYS)
    if xpath:
        expression_value = xpath if xpath.startswith("xpath=") else f"xpath={xpath}"
        same = [item for item in siblings if _first(item, _XPATH_KEYS) == xpath]
        if len(same) != 1:
            return _blocked(
                state_code,
                code,
                risk,
                f"{state_code}/{code} observed XPath is ambiguous.",
            )
        decision = _resolved(
            state_code,
            element,
            "xpath",
            f"locator({json.dumps(expression_value)})",
            {"xpath": xpath},
        )
        decision.xpath_fallback = True
        decision.reason = f"{state_code}/{code} uses an observed XPath fallback."
        return decision

    return _blocked(
        state_code,
        code,
        risk,
        f"{state_code}/{code} has no observed role, label, placeholder, test id, CSS selector, or XPath.",
    )


def _unique_text(
    state_code: str,
    element: dict[str, Any],
    siblings: list[dict[str, Any]],
    strategy: str,
    value: str,
    keys: tuple[str, ...],
    expression: str,
) -> SelectorDecision:
    same = [item for item in siblings if _first(item, keys) == value]
    if len(same) != 1:
        return _blocked(
            state_code,
            str(element.get("element_code") or ""),
            _risk(element),
            (
                f"{state_code}/{element.get('element_code')} observed {strategy} "
                f"{value!r} matches {len(same)} elements."
            ),
        )
    return _resolved(state_code, element, strategy, expression, {strategy: value})


def _resolved(
    state_code: str,
    element: dict[str, Any],
    strategy: str,
    expression: str,
    used: dict[str, Any],
) -> SelectorDecision:
    evidence = {
        "state_code": state_code,
        "element_code": element.get("element_code"),
        "source": element.get("source"),
        "risk": _risk(element),
        **used,
    }
    return SelectorDecision(
        status="resolved",
        strategy=strategy,
        expression=expression,
        evidence=evidence,
        risk=_risk(element),
        state_code=state_code,
        element_code=str(element.get("element_code") or ""),
    )


def _blocked(state_code: str, element_code: str, risk: str, reason: str) -> SelectorDecision:
    return SelectorDecision(
        status="blocked",
        reason=reason,
        risk=risk or "SAFE",
        state_code=state_code,
        element_code=element_code,
        evidence={"state_code": state_code, "element_code": element_code, "risk": risk},
    )


def _risk(element: dict[str, Any]) -> str:
    value = str(element.get("risk") or "SAFE").upper()
    if value in {"SAFE", "REVIEW", "DESTRUCTIVE"}:
        return value
    return "SAFE"


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _role(element: dict[str, Any]) -> str:
    raw = element.get("role")
    locator = element.get("locator")
    if not isinstance(raw, str) and isinstance(locator, dict):
        raw = locator.get("role")
    if not isinstance(raw, str):
        return ""
    normalized = _ROLE_ALIASES.get(raw.strip(), raw.strip().lower())
    if normalized not in _PLAYWRIGHT_ROLES:
        return ""
    return normalized


def _locator_name(element: dict[str, Any]) -> str:
    locator = element.get("locator")
    if isinstance(locator, dict):
        return _text(locator.get("name"))
    return ""


def _first(element: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(element.get(key))
        if value and not _looks_transient(value):
            return value
    return ""


def _css(element: dict[str, Any]) -> str:
    for key in _CSS_KEYS:
        value = element.get(key)
        if isinstance(value, str) and value.strip() and not _looks_transient(value):
            candidate = value.strip()
            if candidate.startswith(("xpath=", "//", "(//")):
                continue
            if candidate.startswith(("#", ".", "[")) or " " in candidate or ">" in candidate:
                return candidate
    locator = element.get("locator")
    if isinstance(locator, str) and locator.strip().startswith(("#", ".", "[")) and not _looks_transient(locator):
        return locator.strip()
    dom_id = element.get("dom_id") or element.get("id")
    if isinstance(dom_id, str) and _stable_id(dom_id):
        return f"#{dom_id}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", dom_id) else f"[id={json.dumps(dom_id)}]"
    return ""


def _stable_id(value: str) -> bool:
    text = value.strip()
    if not text or len(text) > 80 or _UNSTABLE_ID.match(text):
        return False
    return re.search(r"\buid\b", text, re.IGNORECASE) is None


def _looks_transient(value: str) -> bool:
    text = value.strip()
    if re.search(r"\buid[-_=:\s]", text, re.IGNORECASE):
        return True
    return bool(_UNSTABLE_ID.match(text))
