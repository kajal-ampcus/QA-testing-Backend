"""Deterministic checks on generated cases before they can be persisted."""

import re
from typing import Any
from urllib.parse import urlparse

from core.agents.test_design.schemas import TestCaseSpec

_VERB_PATTERNS = {
    "logout": r"\b(log\s*out|sign\s*out|end\s*session)\b",
    "add": r"\badd\b", "remove": r"\b(remove|delete|clear)\b",
    "filter": r"\b(filter|search|sort)\b", "mark": r"\bmark\b",
    "place": r"\bplace(?:s|d|ing)?\s+(?:an?\s+)?order\b",
    "navigate": r"\b(navigate|go\s+to)\b",
    "redirect": r"\b(redirect(?:s|ed|ing)?|navigate(?:s|d|ing)?)\b",
    "open": r"\b(open(?:s|ed|ing)?|view(?:s|ed|ing)?|display(?:s|ed|ing)?|show(?:s|ed|ing)?)\b",
    "submit": r"\b(submit(?:s|ted|ting)?|place(?:s|d|ing)?\s+(?:an?\s+)?order|confirm(?:s|ed|ing)?)\b",
    # "login page/screen/form" names a surface; it is not an authentication
    # action.  The negative lookahead is local so a later genuine login in the
    # same AC is still found.
    "login": r"\b(log\s*in|sign\s*in)\b(?!\s+(?:screen|page|form)\b)",
}


def _ac_verbs(ac_text: str) -> list[str]:
    return [verb for verb, pattern in _VERB_PATTERNS.items() if re.search(pattern, ac_text, re.I)]


def validate_case(case: TestCaseSpec, states: list[dict[str, Any]], ac_ids: set[str], ac_texts: dict[str, str] | None = None) -> list[str]:
    issues: list[str] = []
    by_code = {s["state_code"]: s for s in states}
    ac_texts = ac_texts or {}
    if not case.steps:
        return ["Test case has no steps."]
    for expected, step in enumerate(case.steps, 1):
        if step.step_number != expected:
            issues.append(f"Step {expected}: step_number={step.step_number} after renumbering.")
    if case.steps[-1].action != "assert":
        issues.append("Last step must be an assert verifying the expected result; a click or fill alone is not a complete test.")
    bad_acs = set(case.traceability) - ac_ids
    if bad_acs:
        issues.append(f"Traceability contains unknown AC ids: {sorted(bad_acs)}.")

    previous_action_state = None
    submitted = interactive_action_seen = False
    for number, step in enumerate(case.steps, 1):
        state = by_code.get(step.target.state_code)
        if state is None:
            issues.append(f"Step {number}: state_code '{step.target.state_code}' not in map.")
            continue
        element = next((e for e in state.get("elements", []) if e.get("element_code") == step.target.element_code), None)
        if step.target.element_code and element is None:
            issues.append(f"Step {number}: element_code '{step.target.element_code}' not in state '{step.target.state_code}'.")
        if step.action in {"fill", "select", "click"} and step.target.element_code and element is None:
            issues.append(f"Step {number}: {step.action} requires an observed element.")
        if element:
            for field, key in (("element_name", "name"), ("element_role", "role")):
                value = getattr(step.target, field)
                if value and value != element.get(key):
                    issues.append(f"Step {number}: {field} '{value}' differs from observed '{element.get(key)}'.")
            if step.action in {"fill", "select", "click"} and (element.get("disabled") or element.get("visible") is False):
                issues.append(f"Step {number}: cannot act on disabled/hidden element.")
        for key in re.findall(r"\{([^{}]+)\}", step.value or ""):
            data: Any = case.test_data
            if isinstance(data, dict) and key in data:
                continue
            for part in key.split("."):
                if not isinstance(data, dict) or part not in data:
                    issues.append(f"Step {number}: test_data path {{{key}}} cannot be resolved.")
                    break
                data = data[part]
        if step.action == "assert":
            expected_text = (step.expected or "").strip()
            if not expected_text:
                issues.append(f"Step {number}: assert step has no expected value.")
            if re.search(r"\b(note:|not mapped|not explicitly mapped|e\.g\.|implying|would verify)\b", expected_text, re.I):
                issues.append(f"Step {number}: keep explanations out of assert expected value.")
            if interactive_action_seen and re.search(r"\bcurrent URL includes\b", expected_text, re.I) and previous_action_state == step.target.state_code:
                issues.append(f"Step {number}: the unchanged page URL does not prove the preceding interaction succeeded or was rejected. Assert an observed result instead.")
            if "url" in expected_text.lower():
                for path in re.findall(r"https?://[^\s'\"<>]+|(?<![\w/])/[\w/-]+", expected_text):
                    expected_path = urlparse(path.rstrip(".,)")).path
                    if expected_path and expected_path not in (state.get("url_pattern", "") or ""):
                        issues.append(f"Step {number}: expected URL path '{expected_path}' does not match observed state path '{state.get('url_pattern', '')}'.")
            if submitted and previous_action_state == step.target.state_code and element and element.get("role") in {"StaticText", "paragraph", "heading"} and expected_text.strip("'\"") in (element.get("name") or ""):
                issues.append(f"Step {number}: pre-existing page text cannot prove submission succeeded; use an observed result state.")
        elif step.action in {"click", "fill", "select", "navigate"}:
            previous_action_state = step.target.state_code
            if step.action in {"click", "fill", "select"}:
                interactive_action_seen = True
            if step.action == "click" and element:
                submitted = bool(re.search(r"\b(submit|send|save|log.?in|sign.?in)\b", element.get("name") or "", re.I))
                if element.get("url"):
                    next_step = case.steps[number] if number < len(case.steps) else None
                    if next_step and next_step.action == "assert":
                        target_state = by_code.get(next_step.target.state_code)
                        href_path = urlparse(element["url"]).path
                        if target_state and href_path and next_step.target.state_code != step.target.state_code and href_path != target_state.get("url_pattern", ""):
                            issues.append(f"Step {number}: link destination and asserted state disagree.")

    # An availability or assertion-only case is not claiming to perform a UI
    # action.  Only compare AC verbs when the candidate actually interacts
    # with a discovered control; this still catches a logout criterion whose
    # case clicks LOGIN.
    has_control_action = any(step.action in {"click", "fill", "select"} for step in case.steps)
    if has_control_action:
        def step_text(step: Any) -> str:
            state = by_code.get(step.target.state_code, {})
            element = next(
                (item for item in state.get("elements", []) if item.get("element_code") == step.target.element_code),
                {},
            )
            return f"{step.action} {step.target.element_name or element.get('name') or ''} {step.expected or ''}"

        action_text = " ".join(step_text(step) for step in case.steps)
        for ac_id in case.traceability:
            for verb in _ac_verbs(ac_texts.get(ac_id, "")):
                if not re.search(_VERB_PATTERNS[verb], action_text, re.I):
                    issues.append(f"Case does not perform the '{verb}' action the criterion requires.")
    asserts = [step for step in case.steps if step.action == "assert"]
    if asserts and all(
        not step.target.element_code
        and "url" in (step.expected or "").lower()
        and not str((step.assertion or {}).get("kind") or "")
        for step in asserts
    ):
        issues.append("The only assertion checks the URL. Assert the observed content or element that proves the expected result.")
    return list(dict.fromkeys(issues))
