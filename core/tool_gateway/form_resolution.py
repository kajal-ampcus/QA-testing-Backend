"""Exact, scoped discovery input resolution. Values never belong in checkpoints.

This service is the only discovery-facing entry point for candidate retrieval.
An ordinary crawl has no test-case context, so case fixtures are ineligible.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from core.agents.application_discovery.form_inputs import (
    describe_field,
    is_ephemeral_field,
    normalize_name,
)


@dataclass(frozen=True)
class FormContext:
    project_id: UUID
    page_key: str
    form_key: str
    credential_ref: str | None = None
    login: bool = False
    test_case_id: UUID | None = None
    scenario: str | None = None
    form_group: str | None = None
    page_url: str | None = None


@dataclass
class Resolution:
    values: dict[str, str] = field(default_factory=dict, repr=False)
    missing: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def populated(node: dict[str, Any]) -> bool:
    if node.get("role") in {"checkbox", "radio"} or node.get("input_type") in {"checkbox", "radio"}:
        return bool(node.get("checked") or node.get("user_modified"))
    return bool(
        node.get("has_value")
        or node.get("user_modified")
        or node.get("checked")
        or node.get("selected")
        or node.get("value") not in (None, "")
    )


def valid_value(node: dict[str, Any], value: Any) -> str | None:
    """Validate against live controls, never coerce an arbitrary option into one."""
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif isinstance(value, (int, float)) and (
        node.get("role") == "spinbutton" or node.get("input_type") == "number"
    ):
        value = str(value)
    if not isinstance(value, str) or value == "":
        return None
    role, kind = node.get("role"), node.get("input_type")
    if role in {"checkbox", "radio"} or kind in {"checkbox", "radio"}:
        return value.lower() if value.lower() in {"true", "false"} else None
    if role == "combobox" or kind == "select":
        items = node.get("option_items")
        if items:
            matches = [
                item
                for item in items
                if item.get("enabled", True) and value in {item.get("label"), item.get("value")}
            ]
            return (
                str(matches[0].get("value", matches[0].get("label"))) if len(matches) == 1 else None
            )
        options = node.get("options") or []
        return value if options.count(value) == 1 else None
    if role not in {"textbox", "input", "searchbox", "spinbutton"}:
        return None
    if kind not in {
        None,
        "",
        "text",
        "password",
        "email",
        "number",
        "tel",
        "search",
        "url",
        "date",
        "time",
        "datetime-local",
    }:
        return None
    if kind in {"date", "time", "datetime-local"}:
        try:
            {"date": date, "time": time, "datetime-local": datetime}[kind].fromisoformat(value)
        except ValueError:
            return None
    if kind == "url" and urlsplit(value).scheme not in {"http", "https"}:
        return None
    if kind == "number" or role == "spinbutton":
        try:
            number = Decimal(value)
            if not number.is_finite():
                return None
            if node.get("min") not in (None, "") and number < Decimal(str(node["min"])):
                return None
            if node.get("max") not in (None, "") and number > Decimal(str(node["max"])):
                return None
        except InvalidOperation:
            return None
    if kind == "email" and ("@" not in value or any(c.isspace() for c in value)):
        return None
    return value


def resolve_candidates(nodes: list[dict[str, Any]], candidates: dict[str, list[Any]]) -> Resolution:
    result = Resolution()
    counts = Counter(describe_field(node)["key"] for node in nodes)
    for node in nodes:
        descriptor = describe_field(node)
        key = descriptor["key"]
        if node.get("disabled") or node.get("readonly") or node.get("visible") is False:
            continue
        if populated(node):
            continue
        raw = candidates.get(key, [])
        ephemeral = is_ephemeral_field(descriptor["name"]) or is_ephemeral_field(key)
        valid = [valid_value(node, value) for value in raw]
        conflict = counts[key] != 1 or (bool(raw) and (None in valid or len(set(valid)) != 1))
        if conflict and not ephemeral:
            result.conflicts.append(key)
        if raw and not conflict and not ephemeral:
            result.values[key] = valid[0]
        elif descriptor["required"]:
            result.missing.append(descriptor)
    radio_groups: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        if node.get("role") == "radio" and node.get("radio_group"):
            radio_groups.setdefault(node["radio_group"], []).append(node)
    for group in radio_groups.values():
        selected = [
            node for node in group if result.values.get(describe_field(node)["key"]) == "true"
        ]
        if len(selected) > 1 or (selected and any(populated(node) for node in group)):
            for node in selected:
                descriptor = describe_field(node)
                result.values.pop(descriptor["key"], None)
                result.conflicts.append(descriptor["key"])
                if descriptor["required"]:
                    result.missing.append(descriptor)
    return result


def account_candidates(secret: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, list[Any]]:
    candidates: dict[str, list[Any]] = {}
    aliases = {
        "username": "username",
        "user name": "username",
        "email": "username",
        "email address": "username",
        "user id": "username",
        "password": "password",
        "employee id": "username",
        "staff id": "username",
        "userid": "username",
        "enter your password": "password",
        "your password": "password",
    }
    for node in nodes:
        descriptor = describe_field(node)
        name = normalize_name(descriptor["name"])
        for item in secret.get("fields") or []:
            if normalize_name(str(item.get("name") or "")) == name:
                candidates.setdefault(descriptor["key"], []).append(item.get("value"))
        source = aliases.get(name)
        for slot in ("username", "password"):
            if (
                secret.get(slot + "_selector")
                and normalize_name(secret[slot + "_selector"]) == name
            ):
                source = slot
        if source and secret.get(source):
            candidates.setdefault(descriptor["key"], []).append(secret[source])
    return candidates


async def resolve_form(context: FormContext, nodes: list[dict[str, Any]]) -> Resolution:
    from core.tool_gateway.secret_resolver import resolve_login
    from infra.db.repositories.form_answer_repo import get_form_answers
    from infra.db.repositories.form_test_data_repo import discovery_fixture

    candidates: dict[str, list[Any]] = {}
    answers = None
    if not context.login or context.credential_ref:
        answers = await get_form_answers(
            context.project_id,
            context.page_key,
            context.form_key,
            context.credential_ref,
            login=context.login,
        )
    for key, value in (answers or {}).items():
        candidates.setdefault(key, []).append(value)
    if context.login and context.credential_ref:
        secret = await resolve_login(context.credential_ref, context.project_id)
        if context.page_url and secret.get("login_url"):
            saved_url, live_url = urlsplit(secret["login_url"]), urlsplit(context.page_url)
            if (
                (saved_url.scheme and saved_url.scheme != live_url.scheme)
                or (saved_url.netloc and saved_url.netloc.lower() != live_url.netloc.lower())
                or (saved_url.path.rstrip("/") != live_url.path.rstrip("/"))
            ):
                secret = {}  # A different login form is not an eligible account context.
        for key, values in account_candidates(secret, nodes).items():
            candidates.setdefault(key, []).extend(values)
    fixture = await discovery_fixture(context)
    for node in nodes:
        descriptor = describe_field(node)
        # Role-qualified keys disambiguate control types; no fuzzy fixture names.
        for key, value in fixture.items():
            if key == descriptor["key"] or normalize_name(key) == normalize_name(
                descriptor["name"]
            ):
                candidates.setdefault(descriptor["key"], []).append(value)
    return resolve_candidates(nodes, candidates)


async def apply_values(
    client: Any,
    nodes: list[dict[str, Any]],
    values: dict[str, str],
    *,
    authentication: bool = False,
) -> set[str]:
    """Reinspect before filling; preserve values entered since resolution."""
    from core.tool_gateway.snapshot import parse_elements

    applied: set[str] = set()
    for field_node in nodes:
        key = describe_field(field_node)["key"]
        if key not in values:
            continue
        snapshot = await client.take_snapshot()
        live = (
            await client.inspect_elements(snapshot)
            if hasattr(client, "inspect_elements")
            else parse_elements(snapshot)
        )
        matches = [node for node in live if describe_field(node)["key"] == key]
        if len(matches) != 1:
            continue
        node = matches[0]
        if populated(node):
            applied.add(key)
            continue
        if (
            node.get("disabled")
            or node.get("readonly")
            or node.get("visible") is False
            or not node.get("uid")
        ):
            continue
        value = valid_value(node, values[key])
        if value is None:
            continue
        if hasattr(client, "set_next_action_context"):
            client.set_next_action_context(
                f"Fill saved input: {node.get('name') or 'field'}", sensitive=True
            )
        if node.get("role") in {"checkbox", "radio"} or node.get("input_type") in {
            "checkbox",
            "radio",
        }:
            if (
                node.get("role") == "radio"
                and node.get("radio_group")
                and any(
                    other.get("radio_group") == node["radio_group"] and populated(other)
                    for other in live
                )
            ):
                continue
            if value == "true":
                await client.click(str(node["uid"]))
        elif authentication and hasattr(client, "_fill_authentication_field"):
            await client._fill_authentication_field(
                str(node["uid"]), value, [node["name"]], {node["role"]}, node["name"]
            )
        else:
            await client.fill(str(node["uid"]), value)
        applied.add(key)
    return applied
