"""Case type, typed values, and a short fix note for an execution result.

Pass or fail still comes from Playwright. This module only explains the
result. It does not call a model.
"""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE = re.compile(
    r"password|passwd|secret|token|api[_-]?key|credential|authorization|\bpwd\b",
    re.IGNORECASE,
)


def input_fields(test_data: dict[str, Any] | None, steps: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Field names and values the spec types, with secrets masked."""
    fields: list[dict[str, str]] = []
    for step in steps or []:
        if str(step.get("action") or "") != "fill":
            continue
        target = step.get("target") if isinstance(step.get("target"), dict) else {}
        name = str(target.get("element_name") or target.get("element_code") or "field")
        fields.append({"name": name, "value": _mask(name, str(step.get("value") or ""))})
    if not fields:
        for path, value in _leaves(test_data or {}):
            name = path.split(".")[-1] or path or "field"
            fields.append({"name": name, "value": _mask(name, value)})
    return fields[:12]


def explain_failure(status: str, error: str | None, expected: str, actual: str) -> tuple[str, str]:
    """Return a cause and a recommended fix. Empty strings when the check passed."""
    if status.lower() not in {"failed", "error"}:
        return "", ""
    text = f"{error or ''} {actual}".lower()
    if any(token in text for token in ("timeout", "timed out", "timedout")):
        return (
            "The page or element was not ready in time.",
            "Confirm the application URL is reachable and the screen still matches the application map.",
        )
    if any(token in text for token in ("locator", "waiting for", "strict mode", "not found", "resolved to")):
        return (
            "Playwright could not find the element.",
            "Regenerate the suite after a fresh discovery so the selector matches the current screen.",
        )
    if any(token in text for token in ("401", "403", "login", "unauthorized", "credential", "password")):
        return (
            "Sign-in did not succeed.",
            "Check the account saved under Application Access, then run the suite again.",
        )
    if any(token in text for token in ("net::", "err_", "navigation", "enotfound", "econnrefused")):
        return (
            "The browser could not open the page.",
            "Confirm the project application URL is correct and the site is up.",
        )
    if expected and actual and expected not in {"pass", "skipped"} and expected != actual:
        return (
            "The page text did not match the expected result.",
            "Compare the expected text with the screenshot. Update the test case if the requirement changed, or fix the application if the text is wrong.",
        )
    return (
        "The check failed.",
        "Open the screenshot and console log, then compare the step with the approved test case.",
    )


def _mask(name: str, value: str) -> str:
    if not value:
        return ""
    if _SENSITIVE.search(name) or _SENSITIVE.search(value):
        return "••••"
    return value


def _leaves(data: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                walk(f"{prefix}.{key}" if prefix else str(key), item)
        elif isinstance(value, str):
            found.append((prefix, value))

    walk("", data)
    return found
