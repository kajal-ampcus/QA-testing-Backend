"""Map-grounded test cases without waiting on a slow reasoning LLM.

gpt-oss-20b regularly exceeds 120s for even two structured cases, which made
every AC time out. These drafts use only observed states and elements so they
pass deterministic validation and return in seconds.
"""

from __future__ import annotations

import re
from typing import Any

from core.agents.test_design.schemas import StepTarget, TestCaseSpec, TestStep

_FILLABLE = {"textbox", "searchbox", "spinbutton", "combobox"}
_CLICKABLE = {"button", "link", "checkbox", "radio"}
_SUBMIT = re.compile(r"\b(submit|send|save|log.?in|sign.?in|continue|next)\b", re.I)


def _observed(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        element
        for element in state.get("elements", [])
        if element.get("element_code")
        and not element.get("disabled")
        and element.get("visible") is not False
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


def _primary_click(elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    clicks = [element for element in elements if _usable(element, _CLICKABLE)]
    submits = [element for element in clicks if _SUBMIT.search(str(element.get("name") or ""))]
    return (submits or clicks or [None])[0]


def _fill_value(element: dict[str, Any], *, valid: bool) -> tuple[str, str, str]:
    name = str(element.get("name") or element.get("element_code") or "field")
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "field"
    role_name = f"{name} {element.get('type') or ''} {element.get('role') or ''}".lower()
    if "email" in role_name:
        value = "person@example.test" if valid else "not-an-email"
    elif "pass" in role_name:
        value = "ValidPass1!" if valid else "wrong"
    elif "phone" in role_name or "mobile" in role_name:
        value = "9999999999" if valid else "abc"
    else:
        value = "valid-input" if valid else ""
    return key, name, value


def _build_case(
    *,
    ac: dict[str, Any],
    category: str,
    start: dict[str, Any],
    result: dict[str, Any],
    title: str,
) -> TestCaseSpec | None:
    elements = _observed(start)
    fills = [element for element in elements if _usable(element, _FILLABLE)][:3]
    click = _primary_click(elements)
    valid = category == "POSITIVE"
    steps: list[TestStep] = [
        TestStep(step_number=1, action="navigate", target=_target(start)),
    ]
    test_data: dict[str, str] = {}
    for element in fills:
        key, _name, value = _fill_value(element, valid=valid)
        test_data[key] = value
        steps.append(
            TestStep(
                step_number=len(steps) + 1,
                action="fill",
                target=_target(start, element),
                value=f"{{{key}}}",
            )
        )
    if click and (fills or category == "POSITIVE"):
        steps.append(
            TestStep(
                step_number=len(steps) + 1,
                action="click",
                target=_target(start, click),
            )
        )

    if category == "POSITIVE" and result["state_code"] != start["state_code"]:
        assert_state = result
        expected = f"The current URL includes {_url_path(result)}"
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
    kind = "succeeds" if valid else "is rejected"
    return TestCaseSpec(
        title=f"{label[:80]} {kind}",
        objective=f"Verify observed UI for {ac.get('id')} ({category.lower()}).",
        category=category,  # type: ignore[arg-type]
        preconditions=[f"Browser is on {_url_path(start)}"],
        steps=steps,
        expected_result=expected,
        test_data=test_data,
        traceability=[str(ac["id"])],
        confidence=0.7,
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
        ranked = _rank_states(states, ac, requirement_title)
        start = ranked[0]
        result = next((state for state in ranked[1:] if state["state_code"] != start["state_code"]), start)
        wanted = requested_categories.get(ac["id"]) if requested_categories else None
        categories = list(wanted) if wanted else ["POSITIVE", "NEGATIVE"]
        for category in categories:
            if category not in {"POSITIVE", "NEGATIVE", "EDGE_CASE"}:
                continue
            spec = _build_case(
                ac=ac,
                category="NEGATIVE" if category == "EDGE_CASE" else category,
                start=start,
                result=result,
                title=requirement_title,
            )
            if spec is not None:
                if category == "EDGE_CASE":
                    spec.category = "EDGE_CASE"
                    spec.title = f"{str(ac.get('text') or ac['id'])[:80]} edge case"
                cases.append(spec)
    return cases
