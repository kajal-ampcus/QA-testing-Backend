"""Deterministic checks on generated cases before they can be persisted."""

import re
from decimal import Decimal, InvalidOperation
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


def validate_case(case: TestCaseSpec, states: list[dict[str, Any]], ac_ids: set[str], ac_texts: dict[str, str] | None = None, *, strict: bool = False, assertion_sources: list[str] | None = None) -> list[str]:
    issues: list[str] = []
    by_code = {s["state_code"]: s for s in states}
    ac_texts = ac_texts or {}
    if strict:
        if len(case.title) > 300:
            issues.append("Test case title exceeds the existing 300-character storage limit.")
        for label, value in (("title", case.title), ("objective", case.objective), ("expected_result", case.expected_result)):
            if not value.strip():
                issues.append(f"Test case {label} must be nonempty.")
        if not case.preconditions or not all(item.strip() for item in case.preconditions):
            issues.append("Test case must have explicit nonempty preconditions.")
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
    last_click = None
    filled_values: dict[tuple[str, str | None], str | None] = {}
    submitted = interactive_action_seen = False
    for number, step in enumerate(case.steps, 1):
        state = by_code.get(step.target.state_code)
        if state is None:
            issues.append(f"Step {number}: state_code '{step.target.state_code}' not in map.")
            continue
        element = next((e for e in state.get("elements", []) if e.get("element_code") == step.target.element_code), None)
        if step.target.element_code and element is None:
            issues.append(f"Step {number}: element_code '{step.target.element_code}' not in state '{step.target.state_code}'.")
        if step.action in {"fill", "select", "click"} and element is None:
            issues.append(f"Step {number}: {step.action} requires an observed element.")
        if strict and step.action in {"fill", "select"} and step.value is None:
            issues.append(f"Step {number}: input value is missing; use an explicit empty string for a blank-input test.")
        if element:
            for field, key in (("element_name", "name"), ("element_role", "role")):
                value = getattr(step.target, field)
                if value and value != element.get(key):
                    issues.append(f"Step {number}: {field} '{value}' differs from observed '{element.get(key)}'.")
            if step.action in {"fill", "select", "click"} and (element.get("disabled") or element.get("visible") is False):
                issues.append(f"Step {number}: cannot act on disabled/hidden element.")
            if strict and step.action == "fill" and element.get("role") not in {"textbox", "searchbox", "spinbutton", "combobox"}:
                issues.append(f"Step {number}: cannot fill a non-input control.")
        if strict and last_click and state["state_code"] != last_click[0]["state_code"] and step.action != "navigate":
            from core.agents.test_design.fast_generate import _result_state

            observed_result, _ = _result_state(states, last_click[0], last_click[1])
            if (observed_result["state_code"] != state["state_code"] and
                (observed_result.get("url_pattern"), observed_result.get("reached_via"), observed_result.get("account_context")) !=
                (state.get("url_pattern"), state.get("reached_via"), state.get("account_context"))):
                issues.append(f"Step {number}: the asserted result is not an observed destination of the preceding action.")
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
            if strict:
                sources = assertion_sources or [ac_texts.get(ac_id, "") for ac_id in case.traceability]
                issues.extend(_assertion_issues(step, element, state, number, sources))
                if last_click and (step.assertion or {}).get("kind") == "url" and state.get("url_pattern") == last_click[0].get("url_pattern"):
                    issues.append(f"Step {number}: an unchanged URL cannot prove a same-page interaction, even under a new state code.")
                if (step.assertion or {}).get("kind") == "validity":
                    identity = (step.target.state_code, step.target.element_code)
                    if identity not in filled_values:
                        issues.append(f"Step {number}: a validity check needs an explicit prior input value.")
                    else:
                        value = filled_values[identity]
                        prop = step.assertion.get("property")
                        predicted = None
                        validation = (element or {}).get("validation") or {}
                        if prop == "valueMissing":
                            predicted = value == ""
                        elif prop == "typeMismatch" and value == "not-an-email":
                            predicted = True
                        elif prop == "tooShort":
                            limit = validation.get("minlength", (element or {}).get("minlength"))
                            if str(limit).isdigit() and isinstance(value, str):
                                predicted = bool(value) and len(value) < int(limit)
                        elif prop in {"rangeUnderflow", "rangeOverflow"}:
                            key = "min" if prop == "rangeUnderflow" else "max"
                            try:
                                limit = Decimal(str(validation.get(key, (element or {}).get(key))))
                                numeric = Decimal(str(value))
                                predicted = numeric < limit if key == "min" else numeric > limit
                            except InvalidOperation:
                                pass
                        if predicted is not None and str(predicted).lower() != step.assertion.get("value"):
                            issues.append(f"Step {number}: validity expectation contradicts the test value and observed rule.")
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
            if strict and submitted and previous_action_state == step.target.state_code and (step.assertion or {}).get("kind") in {"visible", "enabled", "url", "accessible_name"}:
                issues.append(f"Step {number}: pre-existing control availability cannot prove a submission outcome.")
            if strict and last_click and previous_action_state == step.target.state_code and element and (step.assertion or {}).get("kind") in {"visible", "enabled"} and element.get("visible") is not False:
                issues.append(f"Step {number}: pre-existing visibility cannot prove the preceding control action succeeded.")
            if strict and submitted and previous_action_state == step.target.state_code and element and (step.assertion or {}).get("kind") == "text":
                text = step.assertion.get("text", "")
                if text and text in (str(element.get("name") or "") + " " + str(element.get("text") or "")):
                    issues.append(f"Step {number}: pre-existing text cannot prove submission succeeded.")
            if submitted and previous_action_state == step.target.state_code and element and str(element.get("role") or "").lower() in {"statictext", "paragraph", "heading"} and expected_text.strip("'\"") in (element.get("name") or ""):
                issues.append(f"Step {number}: pre-existing page text cannot prove submission succeeded; use an observed result state.")
        elif step.action in {"click", "fill", "select", "navigate"}:
            previous_action_state = step.target.state_code
            if step.action in {"click", "fill", "select"}:
                interactive_action_seen = True
            if step.action == "click" and element:
                last_click = (state, element)
                submitted = bool(re.search(r"\b(submit|send|save|log.?in|sign.?in)\b", element.get("name") or "", re.I))
                if element.get("url"):
                    next_step = case.steps[number] if number < len(case.steps) else None
                    if next_step and next_step.action == "assert":
                        target_state = by_code.get(next_step.target.state_code)
                        href_path = urlparse(element["url"]).path
                        target_path = urlparse(target_state.get("url_pattern", "")).path if target_state else ""
                        if target_state and href_path and next_step.target.state_code != step.target.state_code and href_path != target_path:
                            issues.append(f"Step {number}: link destination and asserted state disagree.")
            else:
                last_click = None
            if step.action == "fill":
                value = step.value
                match = re.fullmatch(r"\{([^{}]+)\}", value or "")
                if match:
                    data = case.test_data
                    key = match.group(1)
                    if key in data:
                        value = data[key]
                    else:
                        for part in key.split("."):
                            data = data.get(part) if isinstance(data, dict) else None
                        value = data
                filled_values[(step.target.state_code, step.target.element_code)] = value

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


def _assertion_issues(step: Any, element: dict | None, state: dict, number: int, sources: list[str]) -> list[str]:
    assertion = step.assertion or {}
    kind = assertion.get("kind")
    allowed = {"visible", "enabled", "text", "required", "invalid", "url", "validity", "attribute", "accessible_name", "manual"}
    issues = []
    if kind not in allowed:
        return [f"Step {number}: a supported structured assertion is required."]
    expected_target = step.target.state_code if kind == "url" else step.target.element_code
    if not expected_target or assertion.get("target") != expected_target:
        issues.append(f"Step {number}: structured assertion target disagrees with its mapped target.")
    if kind != "url" and element is None:
        issues.append(f"Step {number}: assertion requires an observed element.")
        return issues
    element = element or {}
    validation = element.get("validation") or {}
    if kind == 'manual':
        requirement = assertion.get('requirement_text', '').strip()
        def normalize(value):
            return ' '.join(str(value).lower().split()).rstrip('.; ')
        if not requirement or not any(normalize(requirement) in normalize(source) for source in sources):
            issues.append(f'Step {number}: manual outcome must cite a supplied requirement clause.')
        if not assertion.get('text', '').strip() or assertion.get('basis') not in {'missing', 'invalid', 'valid', 'refresh', 'observed_refresh', 'observed_toggle'}:
            issues.append(f'Step {number}: manual verification needs a supported behavior and explicit expected outcome.')
        basis, outcome = assertion.get('basis'), assertion.get('text', '')
        if basis == 'valid' and normalize(outcome) != normalize(requirement):
            issues.append(f'Step {number}: the manual success outcome must match its supplied requirement.')
        if basis == 'invalid':
            from core.agents.test_design.form_behaviors import invalid_expected
            names = assertion.get('fields', '').split('|')
            observed_names = {item.get('name') for item in state.get('elements', [])}
            if not all(name in observed_names for name in names) or outcome != invalid_expected(requirement, names):
                issues.append(f'Step {number}: do not invent an error result for a manual rejection test.')
        if basis == 'refresh' and (outcome != 'The displayed challenge changes after refresh.' or not re.search(r'\brefresh\w*\b', requirement, re.I)):
            issues.append(f'Step {number}: refresh verification must follow an explicit refresh requirement.')
        if basis == 'observed_refresh' and (outcome != 'The displayed challenge changes after refresh.' or not re.search(r'\brefresh\b.*\bcaptcha\b', str(element.get('name') or ''), re.I) or not re.search(r'\bcaptcha\b', requirement, re.I)):
            issues.append(f'Step {number}: inferred refresh behavior requires an observed CAPTCHA refresh control and a CAPTCHA requirement.')
    if kind == "text":
        text = assertion.get("text", "")
        observed = str(element.get("name") or "") + " " + str(element.get("text") or "")
        declared = [literal for source in sources for pair in re.findall(r'"([^"\n]+)"|\x27([^\x27\n]+)\x27', source) for literal in pair if literal]
        if not text.strip() or (text not in observed and text not in declared):
            issues.append(f"Step {number}: expected text was not observed; do not invent result messages.")
    if kind == "required" and not (validation.get("required") or element.get("required")):
        issues.append(f"Step {number}: no observed required constraint supports this assertion.")
    if kind == "invalid" and not (validation.get("invalid") or element.get("invalid") or element.get("aria-invalid") == "true"):
        issues.append(f"Step {number}: no observed invalid marker supports this assertion.")
    if kind == "validity":
        prop = assertion.get("property")
        required_rule = {"valueMissing": "required", "rangeUnderflow": "min", "rangeOverflow": "max", "typeMismatch": "type", "tooShort": "minlength"}.get(prop)
        rule = validation.get(required_rule, element.get(required_rule)) if required_rule else None
        if required_rule == "type":
            rule = element.get("type") or element.get("input_type")
        if rule is None or (required_rule == "required" and not rule) or assertion.get("value") not in {"true", "false"}:
            issues.append(f"Step {number}: native validity assertion has no supported observed constraint.")
        input_type = str(element.get("type") or element.get("input_type") or "text")
        if ((prop == "typeMismatch" and input_type not in {"email", "url"}) or
            (prop in {"rangeUnderflow", "rangeOverflow"} and input_type != "number") or
            (prop == "tooShort" and input_type not in {"text", "password", "email", "url", "search", "tel"})):
            issues.append(f"Step {number}: validity property is not applicable to the observed input type.")
    if kind == "attribute":
        name = assertion.get("name")
        observed = validation.get(name, element.get(name))
        if name == "type":
            observed = element.get("type") or element.get("input_type")
        if name not in {"type", "min", "max", "minlength", "maxlength", "pattern", "readonly", "required"} or observed is None or str(observed) != assertion.get("value"):
            issues.append(f"Step {number}: attribute expectation is not supported by observed metadata.")
    if kind == "accessible_name" and assertion.get("text") != element.get("name"):
        issues.append(f"Step {number}: accessible-name expectation differs from observed evidence.")
    return issues
