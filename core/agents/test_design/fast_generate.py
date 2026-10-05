"""Map-grounded test cases without waiting on a slow reasoning LLM.

gpt-oss-20b regularly exceeds 120s for even two structured cases, which made
every AC time out. These drafts use only observed states and elements so they
pass deterministic validation and return in seconds.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from core.agents.test_data.agent import value_for_element
from core.agents.test_design.schemas import StepTarget, TestCaseSpec, TestStep

_FILLABLE = {"textbox", "searchbox", "spinbutton", "combobox"}
_CLICKABLE = {"button", "link", "checkbox", "radio"}
_SUBMIT = re.compile(r"\b(submit|send|save|log.?in|sign.?in|continue|next)\b", re.I)
_CONTROL_LABEL = re.compile(r"observed_link='([^']*)'|name='([^']*)'", re.I)
_CLICK_NAME = re.compile(r"^click\s+([^()]+)$", re.I)


def _observed(state: dict[str, Any]) -> list[dict[str, Any]]:
    # Destructive controls (delete, pay, ...) never go into generated drafts;
    # a tester must add those cases deliberately.
    return [
        element
        for element in state.get("elements", [])
        if element.get("element_code")
        and not element.get("disabled")
        and element.get("visible") is not False
        and element.get("risk") != "DESTRUCTIVE"
    ]


def _usable(element: dict[str, Any], roles: set[str]) -> bool:
    return (element.get("role") or "") in roles


def _target(state: dict[str, Any], element: dict[str, Any] | None = None) -> StepTarget:
    if element is None:
        return StepTarget(state_code=state["state_code"])
    return StepTarget(
        state_code=state["state_code"],
        element_code=element.get("element_code"),
        element_name=element.get("name"),
        element_role=element.get("role"),
    )


def _state_score(state: dict[str, Any], terms: set[str]) -> int:
    blob = " ".join(
        (
            str(state.get("url_pattern", "")),
            " ".join(map(str, state.get("reached_via", []))),
            " ".join(
                str(element.get("name") or element.get("text") or "")
                for element in _observed(state)
            ),
        )
    ).lower()
    return sum(term in blob for term in terms)


def _rank_states(states: list[dict[str, Any]], ac: dict[str, Any], title: str) -> list[dict[str, Any]]:
    terms = {
        word.lower()
        for word in re.findall(r"[a-zA-Z0-9]{3,}", f"{title} {ac.get('text', '')}")
    }
    return sorted(states, key=lambda state: _state_score(state, terms), reverse=True)


def _url_path(state: dict[str, Any]) -> str:
    pattern = str(state.get("url_pattern") or "/")
    match = re.search(r"/[A-Za-z0-9_/-]*", pattern)
    return match.group(0) if match else "/"


_CHROME = re.compile(r"\b(toggle theme|dark mode|light mode)\b", re.I)


def _best_click(elements: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    """Pick a control the case is about.

    Theme switches are chrome. They are not the action for menu, order,
    notification, or logout cases, even when they are the first button.
    """
    clicks = [element for element in elements if _usable(element, _CLICKABLE)]
    pool = [element for element in clicks if not _CHROME.search(str(element.get("name") or ""))]
    if not pool:
        return None
    terms = {word.lower() for word in re.findall(r"[a-zA-Z0-9]{3,}", text)}

    def score(element: dict[str, Any]) -> int:
        name = str(element.get("name") or "").lower()
        return sum(term in name for term in terms)

    ranked = sorted(pool, key=score, reverse=True)
    if score(ranked[0]) > 0:
        return ranked[0]
    submits = [element for element in pool if _SUBMIT.search(str(element.get("name") or ""))]
    return submits[0] if submits else None


def _route(url: str) -> str:
    text = str(url or "").strip()
    if not text or text.startswith("data:"):
        return ""
    path = urlparse(text).path if "://" in text else text
    return path.rstrip("/") or "/"


def _control_label(step: str) -> str:
    match = _CONTROL_LABEL.search(step)
    if match:
        return next(group for group in match.groups() if group)
    click = _CLICK_NAME.match(step.strip())
    return click.group(1).strip() if click else ""


def _norm_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _result_state(
    states: list[dict[str, Any]], start: dict[str, Any], click: dict[str, Any] | None
) -> tuple[dict[str, Any], float]:
    """The state the click is observed to lead to, with a confidence.

    A link uses its href. A button uses the recorded control name. A word
    that only happens to appear inside another page's URL is not a destination.
    """
    href = _route(str((click or {}).get("url") or ""))
    if href:
        for state in states:
            if state["state_code"] != start["state_code"] and _route(str(state.get("url_pattern") or "")) == href:
                return state, 0.8
        return start, 0.5
    name = _norm_label(str((click or {}).get("name") or ""))
    if len(name) >= 2:
        for state in states:
            path = state.get("reached_via") or []
            label = _norm_label(_control_label(str(path[-1]))) if path else ""
            if state["state_code"] != start["state_code"] and label and label == name:
                return state, 0.8
    return start, 0.5


def _fill_value(element: dict[str, Any], *, category: str) -> tuple[str, str, str]:
    datum = value_for_element(element, category)  # type: ignore[arg-type]
    return datum.key, datum.name, datum.value


def _build_case(
    *,
    ac: dict[str, Any],
    category: str,
    start: dict[str, Any],
    states: list[dict[str, Any]],
    title: str,
) -> TestCaseSpec | None:
    elements = _observed(start)
    fills = [element for element in elements if _usable(element, _FILLABLE)][:3]
    click = _best_click(elements, f"{title} {ac.get('text', '')}")
    if category == "EDGE_CASE" and not fills:
        return None  # nothing to vary; a copy of the NEGATIVE case adds no value
    result, confidence = _result_state(states, start, click)
    valid = category == "POSITIVE"
    steps: list[TestStep] = [
        TestStep(step_number=1, action="navigate", target=_target(start)),
    ]
    test_data: dict[str, str] = {}
    for element in fills:
        key, _name, value = _fill_value(element, category=category)
        test_data[key] = value
        steps.append(
            TestStep(
                step_number=len(steps) + 1,
                action="fill",
                target=_target(start, element),
                value=f"{{{key}}}",
            )
        )
    if click and (fills or valid):
        steps.append(
            TestStep(
                step_number=len(steps) + 1,
                action="click",
                target=_target(start, click),
            )
        )

    if valid and result["state_code"] != start["state_code"]:
        assert_state = result
        expected = f"The current URL includes {_url_path(result)}"
    elif category == "EDGE_CASE":
        assert_state = start
        expected = (
            f"The input is handled without an error page and the URL still includes "
            f"{_url_path(start)} or the observed result state"
        )
    else:
        assert_state = start
        expected = f"The current URL includes {_url_path(start)}"
    steps.append(
        TestStep(
            step_number=len(steps) + 1,
            action="assert",
            target=_target(assert_state),
            expected=expected,
        )
    )
    if len(steps) < 2:
        return None
    label = str(ac.get("text") or ac.get("id") or "scenario")
    kind = {"POSITIVE": "succeeds", "NEGATIVE": "is rejected"}.get(category, "handles boundary input")
    return TestCaseSpec(
        title=f"{label[:80]} {kind}",
        objective=f"Verify observed UI for {ac.get('id')} ({category.lower()}).",
        category=category,  # type: ignore[arg-type]
        preconditions=[f"Browser is on {_url_path(start)}"],
        steps=steps,
        expected_result=expected,
        test_data=test_data,
        traceability=[str(ac["id"])],
        confidence=confidence if valid else 0.6,
    )


def generate_cases_from_map(
    acceptance_criteria: list[dict[str, Any]],
    states: list[dict[str, Any]],
    requirement_title: str,
    requested_categories: dict[str, set[str]] | None = None,
) -> list[TestCaseSpec]:
    """Return one POSITIVE and one NEGATIVE draft per AC from observed map data."""
    if not states:
        return []
    cases: list[TestCaseSpec] = []
    for ac in acceptance_criteria:
        start = _rank_states(states, ac, requirement_title)[0]
        wanted = requested_categories.get(ac["id"]) if requested_categories else None
        categories = sorted(wanted) if wanted else ["POSITIVE", "NEGATIVE"]
        for category in categories:
            if category not in {"POSITIVE", "NEGATIVE", "EDGE_CASE"}:
                continue
            spec = _build_case(
                ac=ac,
                category=category,
                start=start,
                states=states,
                title=requirement_title,
            )
            if spec is not None:
                cases.append(spec)
    return cases
