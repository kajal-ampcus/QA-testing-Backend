"""Shared behavior identity for generation and persistence."""

import json
import re
from typing import Any


def discovery_account_context(fingerprint: str, reached_via: list, nodes: list[dict]) -> str:
    """Separate account identity from the discovered page's content identity."""
    key = next((str(node.get('functional_key') or '') for node in nodes
                if node.get('fingerprint') == fingerprint and str(node.get('functional_key') or '').startswith('authenticated::')), '')
    parts = key.split('::')
    if len(parts) >= 3 and parts[1] and parts[1] != 'public':
        return '::'.join(parts[:2])
    return fingerprint if any('role=authentication' in str(action) for action in reached_via or []) else ''


def case_signature(version_data: dict[str, Any], states: list[dict[str, Any]] | None = None) -> tuple[Any, ...]:
    """Semantic identity used to avoid saving relabelled copies.

    Category and traceability are deliberately excluded: identical actions and
    assertions are duplicates even when one case is traced to a second AC or
    labelled with a different category.
    """
    data = version_data.get("test_data") or {}
    by_code = {state["state_code"]: state for state in states or []}

    def target_identity(target: dict[str, Any]) -> tuple[Any, ...]:
        state = by_code.get(target.get("state_code"))
        if state is None:
            return (target.get("state_code"), target.get("element_code"))
        # Codes are map-local. Use observed page/path + control identity across
        # rediscovery, preserving different branches and different destinations.
        page = (state.get("url_pattern"), tuple(state.get("reached_via") or []), state.get("account_context") or "")
        code = target.get("element_code")
        if not code:
            return (page, None)
        element = next((item for item in state.get("elements", []) if item.get("element_code") == code), None)
        if element is None:
            return (page, code)
        identity = {key: element.get(key) for key in ("role", "name", "url")}
        identity["type"] = element.get("type") or element.get("input_type")
        if element.get("role") != "link":
            identity["locator"] = element.get("locator") or element.get("locator_strategy") or element.get("dom_id")
            identity["scope"] = element.get("css_selector") or element.get("dom_id") or element.get("locator_scope") or element.get("control_group")
        return (page, json.dumps(identity, sort_keys=True, ensure_ascii=True))

    def resolved_value(value: Any) -> str:
        def lookup(match: re.Match[str]) -> str:
            key = match.group(1)
            resolved = data
            if isinstance(resolved, dict) and key in resolved:
                resolved = resolved[key]
            else:
                for part in key.split("."):
                    if not isinstance(resolved, dict) or part not in resolved:
                        return match.group(0)
                    resolved = resolved[part]
            return json.dumps(resolved, sort_keys=True, ensure_ascii=True)

        if isinstance(value, str):
            # Preserve intentional whitespace and distinguish typed values.
            match = re.fullmatch(r"\{([^{}]+)\}", value)
            if match:
                return lookup(match)
            # Embedded substitutions must compare with their literal equivalent,
            # without introducing JSON quotes into the surrounding text.
            def embedded(match: re.Match[str]) -> str:
                resolved = lookup(match)
                if resolved == match.group(0):
                    return resolved
                return str(json.loads(resolved))

            value = re.sub(r"\{([^{}]+)\}", embedded, value)
        return json.dumps(value, sort_keys=True, ensure_ascii=True)

    rows = list(
            (
                step.get("action"),
                target_identity(step.get("target") or {}),
                resolved_value(step.get("value")),
                # Structured assertions define behavior; prose is descriptive.
                json.dumps({key: value for key, value in step["assertion"].items() if key not in {"target", "requirement_text"}}, sort_keys=True)
                if step.get("assertion") else step.get("expected"),
            )
            for step in version_data.get("steps", [])
    )
    normalized = []
    assertions = set()
    for row in rows:
        if row[0] == "assert":
            assertions.add(row)
        else:
            normalized.extend(sorted(assertions, key=repr))
            assertions.clear()
            normalized.append(row)
    normalized.extend(sorted(assertions, key=repr))
    return (tuple(normalized),)
