"""Explore dropdowns inside discovery. This is not a separate agent.

Option lists and dependency notes are stored on the application map. A
screenshot is recorded only when the page structure actually changes.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from core.agents.application_discovery.crawler import ClickStep, CrawlBudget
from core.agents.application_discovery.fingerprint import canonical_route, functional_page_key
from core.agents.application_discovery.form_inputs import (
    field_key,
    is_captcha_field,
    is_destructive_control,
    is_ephemeral_field,
    normalize_name,
)
from core.policy_safety.destructive_action_lexicon import classify_risk
from core.tool_gateway.snapshot import parse_elements
from domain.enums import RiskLevel

logger = logging.getLogger(__name__)

_CUSTOM_NAME = re.compile(r"\b(select|dropdown|choose)\b", re.I)
_OPTION_ROLES = {"option", "menuitem"}

RecordChange = Callable[[object, ClickStep], Awaitable[Any]]


def _option_label(option: dict[str, Any] | str) -> str:
    if isinstance(option, str):
        return option.strip()
    return str(option.get("label") or option.get("value") or option.get("name") or "").strip()


def _option_enabled(option: dict[str, Any] | str) -> bool:
    if isinstance(option, str):
        return True
    return option.get("enabled", True) is not False and option.get("disabled") is not True


def option_entries(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw:
        label = _option_label(item)
        if not label or is_destructive_control(label) or is_ephemeral_field(label):
            continue
        value = label if isinstance(item, str) else str(item.get("value") or label)
        entries.append({"label": label, "value": value, "enabled": _option_enabled(item)})
    return entries


def classify_dropdown(element: dict[str, Any]) -> str | None:
    """Return native, combobox, listbox, searchable, or custom. None means skip."""
    role = str(element.get("role") or "")
    name = str(element.get("name") or "")
    if element.get("disabled") or element.get("visible") is False:
        return None
    if is_captcha_field(name) or is_ephemeral_field(name) or is_destructive_control(name):
        return None
    if classify_risk(role, name) == RiskLevel.DESTRUCTIVE:
        return None
    items = option_entries(element.get("option_items") or element.get("options"))
    if role == "combobox" and items and (
        element.get("input_type") == "select" or element.get("options")
    ):
        return "native"
    if role == "combobox" and _CUSTOM_NAME.search(name) and element.get("searchable"):
        return "searchable"
    if role == "combobox" and any(token in name.casefold() for token in ("search", "filter")):
        return "searchable"
    if role == "combobox":
        return "combobox"
    if role == "listbox":
        return "listbox"
    if role == "button" and (_CUSTOM_NAME.search(name) or element.get("expanded")):
        return "custom"
    return None


def option_signature(options: list[dict[str, Any]]) -> str:
    raw = "|".join(
        f"{normalize_name(item['label'])}:{int(bool(item.get('enabled', True)))}"
        for item in options
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def dependency_signature(dependencies: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in dependencies:
        revealed = ",".join(sorted(str(key) for key in item.get("reveals") or []))
        parts.append(
            f"{normalize_name(str(item.get('option') or ''))}:{revealed}:{item.get('navigates_to') or ''}"
        )
    return hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()[:16]


def structure_signature(elements: list[dict[str, Any]], url: str) -> str:
    """Page structure without the selected option, so a harmless selection is not a new screen."""
    parts: list[str] = []
    for element in elements:
        role = str(element.get("role") or "")
        if role in {"image", "StaticText", "InlineTextBox", "option"}:
            continue
        parts.append(
            "|".join(
                (
                    role,
                    normalize_name(str(element.get("name") or "")),
                    "1" if element.get("required") else "0",
                    "1" if element.get("disabled") else "0",
                )
            )
        )
    return hashlib.sha256((canonical_route(url) + "\n" + "\n".join(sorted(parts))).encode()).hexdigest()


def meaningful_change(before: list[dict[str, Any]], after: list[dict[str, Any]], before_url: str, after_url: str) -> bool:
    if canonical_route(before_url) != canonical_route(after_url):
        return True
    return structure_signature(before, before_url) != structure_signature(after, after_url)


def diff_controls(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    def keys(elements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for element in elements:
            role = str(element.get("role") or "")
            name = str(element.get("name") or "")
            if role in {"option", "StaticText", "InlineTextBox", "image"} or not name:
                continue
            found[field_key(role, name)] = element
        return found

    previous = keys(before)
    current = keys(after)
    revealed = sorted(set(current) - set(previous))
    hidden = sorted(set(previous) - set(current))
    validation = sorted(
        key
        for key, element in current.items()
        if key in previous
        and (
            bool(element.get("required")) != bool(previous[key].get("required"))
            or bool(element.get("disabled")) != bool(previous[key].get("disabled"))
        )
    )
    return {"reveals": revealed, "hides": hidden, "validation": validation}


def reuse_dropdown(previous: dict[str, Any] | None, options: list[dict[str, Any]]) -> bool:
    if not previous or previous.get("inaccessible_reason"):
        return False
    enabled_labels = {
        item["label"] for item in options if item.get("enabled", True)
    }
    explored_labels = set(previous.get("explored_options") or [])
    return (
        option_signature(option_entries(previous.get("options") or [])) == option_signature(options)
        and enabled_labels <= explored_labels
    )


def representative_option(options: list[dict[str, Any]], text: str = "") -> str | None:
    enabled = [item for item in options if item.get("enabled", True)]
    folded = text.casefold()
    for item in enabled:
        label = item["label"]
        if len(label) >= 2 and label.casefold() in folded:
            return label
    return enabled[0]["label"] if enabled else None


def behavior_groups(dependencies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One group per distinct revealed UI, not one group per option."""
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in dependencies:
        signature = "|".join(
            (
                ",".join(sorted(str(key) for key in item.get("reveals") or [])),
                ",".join(sorted(str(key) for key in item.get("validation") or [])),
                str(item.get("navigates_to") or ""),
            )
        )
        if signature in seen:
            continue
        seen.add(signature)
        groups.append(item)
    return groups


def sample_options(options: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    enabled = [item for item in options if item.get("enabled", True)]
    return enabled if limit <= 0 else enabled[:limit]


def _within_time_limit(started: float, seconds: float) -> bool:
    return seconds <= 0 or time.monotonic() - started <= seconds


def _root_url(elements: list[dict[str, Any]]) -> str:
    root = next((element for element in elements if element.get("role") == "RootWebArea"), {})
    return str(root.get("url") or "")


def _visible_options(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for element in elements:
        if element.get("role") not in _OPTION_ROLES or element.get("disabled"):
            continue
        label = str(element.get("name") or "").strip()
        if not label or is_destructive_control(label):
            continue
        found.append({"label": label, "value": label, "enabled": True})
    return found


def apply_dropdown_metadata(elements: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    by_key = {str(record.get("field_key") or ""): record for record in records}
    for element in elements:
        key = field_key(str(element.get("role") or ""), str(element.get("name") or ""))
        record = by_key.get(key)
        if record is None:
            continue
        validation = element.setdefault("validation", {})
        if isinstance(validation, dict):
            validation["options"] = [item["label"] for item in record.get("options") or [] if item.get("enabled", True)]
        element["dropdown_dependencies"] = record.get("dependencies") or []
        element["dropdown_kind"] = record.get("kind")


async def _read_nodes(client: Any) -> list[dict[str, Any]]:
    snapshot = await client.take_snapshot()
    if hasattr(client, "inspect_elements"):
        try:
            return await client.inspect_elements(snapshot)
        except Exception:
            logger.debug("Dropdown inspection fell back to the accessibility snapshot", exc_info=True)
    return parse_elements(snapshot)


async def _open_and_collect(client: Any, element: dict[str, Any], limit_scrolls: int) -> list[dict[str, Any]]:
    native = option_entries(element.get("option_items") or element.get("options"))
    if native:
        return native
    uid = element.get("uid")
    if not uid:
        return []
    await client.click(str(uid))
    if hasattr(client, "wait_until_ready"):
        await client.wait_until_ready()
    collected = _visible_options(await _read_nodes(client))
    scrolls = 0
    while scrolls < limit_scrolls and hasattr(client, "scroll_open_listbox"):
        moved = await client.scroll_open_listbox()
        if not moved:
            break
        if hasattr(client, "wait_until_ready"):
            await client.wait_until_ready()
        before = {item["label"] for item in collected}
        for item in _visible_options(await _read_nodes(client)):
            if item["label"] not in before:
                collected.append(item)
        if {item["label"] for item in collected} == before:
            break
        scrolls += 1
    return collected


async def _select_option(client: Any, element: dict[str, Any], option: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    uid = str(element.get("uid") or "")
    label = option["label"]
    if kind == "native" and uid:
        # chrome-devtools-mcp selects native <option>s by their visible text,
        # not by the DOM value. CEP, for example, uses UUID values for district
        # labels such as "Akola".
        await client.fill(uid, label)
    else:
        if uid:
            await client.click(uid)
            if hasattr(client, "wait_until_ready"):
                await client.wait_until_ready()
        nodes = await _read_nodes(client)
        match = next(
            (
                node
                for node in nodes
                if node.get("role") in _OPTION_ROLES
                and normalize_name(str(node.get("name") or "")) == normalize_name(label)
                and node.get("uid")
            ),
            None,
        )
        if match is None:
            return nodes
        await client.click(str(match["uid"]))
    if hasattr(client, "wait_until_ready"):
        await client.wait_until_ready()
    return await _read_nodes(client)


async def _restore(client: Any, element: dict[str, Any], original: str | None, kind: str) -> None:
    try:
        if original:
            await _select_option(client, element, {"label": original, "value": original, "enabled": True}, kind)
            return
        if hasattr(client, "press_key"):
            await client.press_key("Escape")
    except Exception:
        logger.info("Could not restore dropdown %s", element.get("name") or element.get("role"))


def _match_previous(previous: list[dict[str, Any]], page_key: str, key: str) -> dict[str, Any] | None:
    for record in previous:
        if record.get("page_key") == page_key and record.get("field_key") == key:
            return record
    return None


async def explore_dropdowns(
    client: Any,
    budget: CrawlBudget,
    *,
    previous: list[dict[str, Any]] | None = None,
    explored: list[dict[str, Any]] | None = None,
    record_change: RecordChange | None = None,
    depth: int = 0,
    page_key: str | None = None,
    skip_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Open each dropdown, sample enabled options, and record real UI changes."""
    if depth > budget.max_dropdown_depth:
        return []
    nodes = await _read_nodes(client)
    page = page_key or functional_page_key(_root_url(nodes), nodes)
    skipped = skip_keys or set()
    seen = {
        (str(item.get("page_key")), str(item.get("field_key")), str(item.get("option_signature")))
        for item in explored or []
    }
    records: list[dict[str, Any]] = []
    started = time.monotonic()
    for element in nodes:
        if not _within_time_limit(started, budget.max_dropdown_seconds):
            logger.info("Dropdown exploration stopped at the time limit on %s", page)
            break
        kind = classify_dropdown(element)
        if kind is None:
            continue
        key = field_key(str(element.get("role") or ""), str(element.get("name") or ""))
        if key in skipped:
            continue
        try:
            options = await _open_and_collect(client, element, limit_scrolls=5)
        except Exception:
            logger.info("Dropdown %s could not be opened", element.get("name"), exc_info=True)
            options = []
        signature = option_signature(options)
        if (page, key, signature) in seen:
            continue
        prior = _match_previous(previous or [], page, key)
        if options and reuse_dropdown(prior, options):
            reused = dict(prior or {})
            reused["version_status"] = "unchanged"
            records.append(reused)
            logger.info("Reused unchanged dropdown %s (%d options)", element.get("name"), len(options))
            continue
        record: dict[str, Any] = {
            "page_key": page,
            "field_key": key,
            "kind": kind,
            "label": str(element.get("name") or ""),
            "options": options,
            "option_signature": signature,
            "dependencies": [],
            "explored_options": [],
            "failed_options": [],
            "inaccessible_reason": None if options else "options_not_readable",
            "version_status": "changed" if prior else "new",
        }
        if not options:
            logger.info("Dropdown %s has no readable options", element.get("name"))
            records.append(record)
            continue
        before = await _read_nodes(client)
        before_url = _root_url(before)
        original = next(
            (
                str(node.get("name") or "")
                for node in before
                if node.get("role") in _OPTION_ROLES and node.get("selected")
            ),
            None,
        )
        for option in sample_options(options, budget.max_dropdown_options):
            if not _within_time_limit(started, budget.max_dropdown_seconds):
                break
            if is_destructive_control(option["label"]):
                continue
            try:
                after = await _select_option(client, element, option, kind)
            except Exception:
                logger.info("Could not select %s", option["label"], exc_info=True)
                record["failed_options"].append(option["label"])
                continue
            record["explored_options"].append(option["label"])
            after_url = _root_url(after)
            change = diff_controls(before, after)
            navigates = functional_page_key(after_url, after) if meaningful_change(before, after, before_url, after_url) else None
            if change["reveals"] or change["hides"] or change["validation"] or navigates:
                record["dependencies"].append(
                    {
                        "option": option["label"],
                        "reveals": change["reveals"],
                        "hides": change["hides"],
                        "validation": change["validation"],
                        "navigates_to": navigates,
                    }
                )
            if navigates and record_change is not None:
                snapshot = await client.take_snapshot()
                await record_change(
                    snapshot,
                    ClickStep(str(element.get("role") or "combobox"), str(element.get("name") or ""), value=option["label"]),
                )
            revealed_dropdown = any(
                item.startswith("combobox:") or item.startswith("listbox:") for item in change["reveals"]
            )
            if revealed_dropdown and depth + 1 <= budget.max_dropdown_depth:
                child_records = await explore_dropdowns(
                    client,
                    budget,
                    previous=previous,
                    explored=[*list(explored or []), *records],
                    record_change=record_change,
                    depth=depth + 1,
                    page_key=page,
                    skip_keys={*skipped, key, *(str(item.get("field_key") or "") for item in records)},
                )
                records.extend(child_records)
            await _restore(client, element, original, kind)
        record["dependency_signature"] = dependency_signature(record["dependencies"])
        if prior and (
            prior.get("option_signature") != signature
            or prior.get("dependency_signature") != record["dependency_signature"]
        ):
            record["version_status"] = "changed"
        records.append(record)
        logger.info(
            "Explored %s dropdown %s with %d options and %d dependencies",
            kind,
            element.get("name"),
            len(options),
            len(record["dependencies"]),
        )
    return records
