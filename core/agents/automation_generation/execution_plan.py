"""Choose a starting state and a minimal step list before Playwright is emitted.

Classification uses preconditions first, then the discovered start state, then
the case wording. It does not key off test codes or a particular application.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.agents.application_discovery.form_inputs import normalize_name
from core.agents.automation_generation.selector_strategy import match_observed_element

_AUTH_PRE = re.compile(
    r"\b(signed in|logged in|authenticated session|already authenticated)\b",
    re.I,
)
_PUBLIC_PRE = re.compile(
    r"\b(logged out|signed out|not signed in|unauthenticated|login page|sign-in page)\b|/login\b",
    re.I,
)
_PASSWORD = re.compile(r"\b(password|passphrase|pin)\b", re.I)
_USERNAME = re.compile(r"\b(username|user name|e-?mail|employee id|user id|login id)\b", re.I)
_CAPTCHA = re.compile(r"\bcaptcha\b", re.I)
_FORGOT = re.compile(r"\b(forgot|reset password|recover)\b", re.I)
_REFRESH = re.compile(r"refresh", re.I)
_SUBMIT = re.compile(r"\b(submit|log.?in|login|sign.?in|continue|next)\b", re.I)
_SUCCESS = re.compile(
    r"\b(succeeds|succeed|success|authenticated|signed in|logged in|welcome)\b",
    re.I,
)
_REJECT = re.compile(r"\b(reject|invalid|incorrect|denied|fails|failure|error)\b", re.I)
_REGISTER = re.compile(r"\b(register|sign up|signup|create account)\b", re.I)
_POSITIVE_VALUES = {
    "valid-input",
    "validpass1!",
    "person@example.test",
    "9999999999",
}


class _State(Protocol):
    state_code: str
    url_pattern: str
    elements: list[dict[str, Any]]


class _Case(Protocol):
    tc_code: str
    title: str
    steps: list[dict[str, Any]]
    test_data: dict[str, Any]
    expected_result: str
    preconditions: list[str]
    category: str
    objective: str


@dataclass
class ExecutionPlan:
    starting_state: str
    fixture: str
    authenticated: bool
    steps: list[dict[str, Any]]
    test_data: dict[str, Any]
    blocked_reason: str | None = None
    notes: list[str] = field(default_factory=list)


def prepare_execution(
    case: _Case, states: dict[str, _State], discovery: dict[str, str] | None = None
) -> ExecutionPlan:
    """Return the steps and fixture that should be rendered for this case."""
    discovery = discovery or {}
    steps = _copy_steps(case.steps)
    data = dict(case.test_data or {})
    starting = classify_start(case, states)
    text = _objective(case)
    start = _start_state(case, states)
    steps, mapping_issues = ground_steps(steps, states, data, discovery)
    if mapping_issues:
        return _blocked(starting, steps, data, "; ".join(mapping_issues))
    start_conflict = _start_conflict(case, start)
    if start_conflict:
        return _blocked(starting, steps, data, start_conflict)
    destructive = _destructive(steps, states)
    kind = _primary_kind(text, case.category, start, steps, states)

    if kind in {"navigate", "refresh"}:
        steps = [step for step in steps if not _unrelated_fill(step, states, text, keep_credentials=False)]
        steps = _ensure_click(steps, start, states, _click_pattern(kind, text))
    elif kind == "validation":
        steps = [step for step in steps if not _unrelated_fill(step, states, text, keep_credentials=True)]
        steps = _ensure_click(steps, start, states, _SUBMIT)
        if not any(step.get("action") in {"fill", "select"} for step in steps):
            steps = _ensure_observed_fill(steps, start, text)
        if not any(step.get("action") in {"fill", "select"} for step in steps):
            return _blocked(starting, steps, data, f"{case.tc_code} is a validation case but the map has no editable control.")
        data, steps = _empty_required(steps, data, text, states)
        if not any(step.get("action") == "fill" and not str(step.get("value") or "").startswith("{discovery:") and _fill_value(step, data) == "" for step in steps):
            return _blocked(starting, steps, data, "Empty-field scenario has no unambiguous empty target; name the field explicitly.")
    elif kind in {"login_success", "login_rejected"}:
        steps = [step for step in steps if not _unrelated_fill(step, states, text, keep_credentials=True)]
        steps = _ensure_click(steps, start, states, _SUBMIT)
        if kind == "login_rejected":
            steps = _ensure_observed_fill(steps, start, text)
            data, steps, negative_issue = _ensure_negative(case, steps, data, states)
            if negative_issue:
                return _blocked(starting, steps, data, negative_issue)
    elif _is_negative(case, text):
        steps = _ensure_observed_fill(steps, start, text)
        data, steps, negative_issue = _ensure_negative(case, steps, data, states)
        if negative_issue:
            return _blocked(starting, steps, data, negative_issue)

    captcha_issue = _captcha_without_value(case, steps, data, states)
    if captcha_issue:
        return _blocked(starting, steps, data, captcha_issue)

    # Required companion fields use configured fixtures only. Their values must
    # not be changed by the negative condition under test.
    if start and _has_click(steps, states, _SUBMIT):
        filled = {step.get("target", {}).get("element_code") for step in steps if step.get("action") in {"fill", "select"}}
        for element in start.elements:
            if not element.get("required") or element.get("element_code") in filled:
                continue
            if str(element.get("role", "")).lower() not in {"textbox", "combobox", "searchbox", "spinbutton"}:
                continue
            name = str(element.get("name") or "")
            value = data.get(name, data.get(name.lower()))
            if not isinstance(value, str) or not value:
                key = normalize_name(name)
                if key not in discovery:
                    return _blocked(starting, steps, data, f"Required companion field {name!r} has no configured test value.")
                value = "{discovery:" + key + "}"
            companion = {"action": "select" if element.get("role") == "combobox" else "fill",
                         "target": {"state_code": start.state_code, "element_code": element["element_code"], "element_name": name},
                         "value": value}
            position = next(i for i, s in enumerate(steps)
                            if s.get("action") == "click" and _SUBMIT.search(_step_name(s, states)))
            steps.insert(position, companion)

    if kind == "login_success" and not _has_click(steps, states, _SUBMIT):
        return _blocked(
            starting,
            steps,
            data,
            f"{case.tc_code} expects authentication to succeed but no login submit control was discovered.",
        )
    if kind == "navigate" and not _has_click(steps, states, _click_pattern(kind, text)):
        return _blocked(
            starting,
            steps,
            data,
            f"{case.tc_code} does not click the control that performs the navigation.",
        )
    if kind == "refresh" and not _has_click(steps, states, _REFRESH):
        return _blocked(
            starting,
            steps,
            data,
            f"{case.tc_code} does not click the control that refreshes the CAPTCHA.",
        )
    if not any(step.get("action") == "assert" for step in steps) and not destructive:
        if case.expected_result and start is not None:
            steps.append(
                {
                    "action": "assert",
                    "target": {"state_code": start.state_code},
                    "expected": case.expected_result,
                }
            )
        else:
            return _blocked(starting, steps, data, f"{case.tc_code} has no assertion for its expected result.")

    return ExecutionPlan(
        starting_state=starting,
        fixture=_fixture(starting),
        authenticated=starting == "AUTHENTICATED",
        steps=_renumber(steps),
        test_data=data,
    )


def ground_steps(steps, states, data, discovery=None):
    """Translate explicit prose actions, retaining approved assertion text and values."""
    issues = []
    current_state = None
    for index, step in enumerate(steps, 1):
        target = step.setdefault("target", {})
        code = target.get("state_code") or current_state
        if code not in states:
            issues.append(f"Step {index}: state {code or '(missing)'} is not observed; specify its mapped page.")
            continue
        target["state_code"] = code
        current_state = code
        action = str(step.get("action", ""))
        text = str(step.get("description") or step.get("instruction") or step.get("expected") or "")
        if action not in {"navigate", "fill", "click", "select", "assert", "wait"}:
            # Some older/imported cases put the entire natural-language step in
            # `action`.  Treat it as prose, rather than trying to emit it as an
            # action name.  This is deliberately limited to the explicit verb
            # forms below; unrecognised prose will still be blocked.
            text = str(action) if action else text
        prose = not target.get("element_code") and (
            action not in {"navigate", "fill", "click", "select", "wait"}
        )
        if prose:
            if re.match(r"^(click|press|tap)\b", text, re.I):
                action = "click"
            elif re.match(r"^(fill|enter|type|leave|clear)\b", text, re.I):
                action = "fill"
            elif re.match(r"^select\b", text, re.I):
                action = "select"
            step["action"] = action
        if (action == "assert" and not target.get("element_code") and not target.get("element_name")
                and (step.get("assertion") or {}).get("kind") != "url"):
            for word, role in (("image", "img"), ("button", "button"), ("heading", "heading")):
                if re.search(rf"\b{word}\b", text, re.I):
                    target.setdefault("element_role", role)
            decision = match_observed_element(code, states[code].elements, target, action, text)
            if decision.status == "resolved":
                target["element_code"] = decision.element_code
                step["mapping"] = decision.evidence
        if action in {"fill", "click", "select"} or target.get("element_code") or target.get("element_name"):
            decision = match_observed_element(code, states[code].elements, target, action, text)
            if decision.status != "resolved":
                issues.append(f"Step {index} ({code}): {decision.reason}")
                continue
            element = next(e for e in states[code].elements if e.get("element_code") == decision.element_code)
            target.update(element_code=decision.element_code, element_name=element.get("name"), element_role=element.get("role"))
            step.setdefault("mapping", decision.evidence)
            if action in {"fill", "select"} and step.get("value") is None:
                empty = re.search(r"\b(empty|blank|clear)\b", text, re.I)
                quotes = re.search(r"""["']([^"']*)["']""", text)
                field = str(element.get("name") or "")
                configured = data.get(field, data.get(field.lower()))
                if empty:
                    step["value"] = ""
                elif quotes:
                    step["value"] = quotes.group(1)
                elif isinstance(configured, str):
                    step["value"] = configured
                elif normalize_name(field) in (discovery or {}):
                    step["value"] = "{discovery:" + normalize_name(field) + "}"
                else:
                    issues.append(f"Step {index} ({code}): no configured value for {field}; configure test data.")
    return steps, issues


def classify_start(case: _Case, states: dict[str, _State]) -> str:
    preconditions = " ".join(case.preconditions or [])
    start = _start_state(case, states)
    if start is not None:
        if _credential_surface(start) and _exercises_auth_surface(case, states):
            return "LOGIN_PAGE"
        return "AUTHENTICATED"
    if _AUTH_PRE.search(preconditions) and not _PUBLIC_PRE.search(preconditions):
        return "AUTHENTICATED"
    if _PUBLIC_PRE.search(preconditions):
        return "PUBLIC"
    text = _objective(case)
    if _credential_language(text) and not _protected_language(text):
        return "LOGIN_PAGE"
    if re.search(r"\b(landing|public|home page)\b", text, re.I):
        return "PUBLIC"
    return "AUTHENTICATED"


def _start_conflict(case: _Case, start: _State | None) -> str | None:
    if start is None:
        return None
    preconditions = " ".join(case.preconditions or [])
    discovered = "LOGIN_PAGE" if _credential_surface(start) else "AUTHENTICATED"
    if _AUTH_PRE.search(preconditions) and discovered == "LOGIN_PAGE":
        return (
            f"{case.tc_code} requires an authenticated session, but discovered start state "
            f"{start.state_code} is a login page."
        )
    if _PUBLIC_PRE.search(preconditions) and discovered == "AUTHENTICATED":
        return (
            f"{case.tc_code} requires a public session, but discovered start state "
            f"{start.state_code} requires an authenticated session."
        )
    return None


def _fixture(starting: str) -> str:
    return "sessionPage" if starting == "AUTHENTICATED" else "publicPage"


def _blocked(starting: str, steps: list[dict[str, Any]], data: dict[str, Any], reason: str) -> ExecutionPlan:
    return ExecutionPlan(
        starting_state=starting,
        fixture=_fixture(starting),
        authenticated=starting == "AUTHENTICATED",
        steps=_renumber(steps),
        test_data=data,
        blocked_reason=reason,
    )


def _objective(case: _Case) -> str:
    return " ".join(
        part
        for part in (case.title, case.objective, case.expected_result, " ".join(case.preconditions or []))
        if part
    )


def _copy_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for step in steps:
        item = dict(step)
        target = item.get("target")
        if isinstance(target, dict):
            item["target"] = dict(target)
        copied.append(item)
    return copied


def _renumber(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, step in enumerate(steps, start=1):
        step["step_number"] = index
    return steps


def _start_state(case: _Case, states: dict[str, _State]) -> _State | None:
    for step in case.steps:
        if str(step.get("action") or "") != "navigate":
            continue
        code = str((step.get("target") or {}).get("state_code") or "")
        if code in states:
            return states[code]
    if not case.steps:
        return None
    code = str((case.steps[0].get("target") or {}).get("state_code") or "")
    return states.get(code)


def _element(step: dict[str, Any], states: dict[str, _State]) -> dict[str, Any] | None:
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    state = states.get(str(target.get("state_code") or ""))
    if state is None:
        return None
    code = str(target.get("element_code") or "")
    for element in state.elements:
        if code and str(element.get("element_code") or "") == code:
            return element
    name = str(target.get("element_name") or "")
    for element in state.elements:
        if name and str(element.get("name") or "") == name:
            return element
    return None


def _credential_surface(state: _State) -> bool:
    for element in state.elements:
        blob = f"{element.get('name') or ''} {element.get('role') or ''} {element.get('type') or ''}"
        if _PASSWORD.search(blob) or _REGISTER.search(blob) or _FORGOT.search(blob):
            return True
        if str(element.get("input_type") or element.get("type") or "").lower() == "password":
            return True
    return False


def _exercises_auth_surface(case: _Case, states: dict[str, _State]) -> bool:
    text = _objective(case)
    if _credential_language(text) or _FORGOT.search(text) or _CAPTCHA.search(text) or _REFRESH.search(text):
        return True
    for step in case.steps:
        element = _element(step, states)
        blob = f"{(element or {}).get('name') or ''} {(step.get('target') or {}).get('element_name') or ''}"
        if _PASSWORD.search(blob) or _USERNAME.search(blob) or _CAPTCHA.search(blob) or _SUBMIT.search(blob) or _FORGOT.search(blob):
            return True
    return False


def _only_opens_entry(case: _Case) -> bool:
    actions = [str(step.get("action") or "") for step in case.steps]
    return actions.count("click") == 0 and actions.count("fill") == 0 and actions.count("select") == 0


def _credential_language(text: str) -> bool:
    return bool(
        _PASSWORD.search(text)
        or _SUBMIT.search(text)
        or _FORGOT.search(text)
        or _CAPTCHA.search(text)
        or _REGISTER.search(text)
        or re.search(r"\b(log.?in|sign.?in|authentication)\b", text, re.I)
    )


def _protected_language(text: str) -> bool:
    return bool(re.search(r"\b(dashboard|profile|orders|notifications|settings|portal)\b", text, re.I))


def _is_negative(case: _Case, text: str) -> bool:
    return (case.category or "").upper() == "NEGATIVE" or bool(_REJECT.search(text))


def _primary_kind(
    text: str,
    category: str,
    start: _State | None,
    steps: list[dict[str, Any]],
    states: dict[str, _State],
) -> str:
    if _CAPTCHA.search(text) and _REFRESH.search(text):
        return "refresh"
    if _FORGOT.search(text):
        return "navigate"
    negative = (category or "").upper() == "NEGATIVE" or bool(_REJECT.search(text))
    on_login = start is not None and _credential_surface(start)
    if on_login and negative and re.search(r"\b(required|empty|missing|validation)\b", text, re.I):
        return "validation"
    if on_login and negative:
        return "login_rejected"
    if on_login and not negative and (_SUCCESS.search(text) or _SUBMIT.search(text)):
        return "login_success"
    if any(_FORGOT.search(_step_name(step, states)) for step in steps):
        return "navigate"
    return "generic"


def _step_name(step: dict[str, Any], states: dict[str, _State]) -> str:
    element = _element(step, states)
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    return str((element or {}).get("name") or target.get("element_name") or "")


def _click_pattern(kind: str, text: str) -> re.Pattern[str]:
    if kind == "refresh":
        return _REFRESH
    if _FORGOT.search(text):
        return _FORGOT
    return _SUBMIT


def _unrelated_fill(
    step: dict[str, Any],
    states: dict[str, _State],
    text: str,
    *,
    keep_credentials: bool,
) -> bool:
    if str(step.get("action") or "") not in {"fill", "select"}:
        return False
    name = _step_name(step, states)
    credential = bool(_USERNAME.search(name) or _PASSWORD.search(name) or _CAPTCHA.search(name))
    if credential:
        return not keep_credentials
    return not (name and len(name) >= 3 and name.casefold() in text.casefold())

_CLICKABLE_ROLES = {"button", "link", "checkbox"}


def _click_role(step: dict[str, Any], states: dict[str, _State]) -> str:
    element = _element(step, states) or {}
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    return str(element.get("role") or target.get("element_role") or "").lower()

def _has_click(steps, states, pattern) -> bool:
    return any(
        step.get("action") == "click"
        and _click_role(step, states) in _CLICKABLE_ROLES
        and pattern.search(_step_name(step, states))
        for step in steps
)

def _ensure_click(
    steps: list[dict[str, Any]],
    start: _State | None,
    states: dict[str, _State],
    pattern: re.Pattern[str],
) -> list[dict[str, Any]]:
    if start is None or _has_click(steps, states, pattern):
        return steps
    candidates = [
            item
            for item in start.elements
            if item.get("element_code")
            and item.get("risk") != "DESTRUCTIVE"
            and str(item.get("role") or "").lower() in _CLICKABLE_ROLES
            and not item.get("disabled") and item.get("visible") is not False
            and pattern.search(str(item.get("name") or ""))
    ]
    if len(candidates) != 1:
        return steps
    element = candidates[0]
    click = {
        "action": "click",
        "target": {
            "state_code": start.state_code,
            "element_code": element.get("element_code"),
            "element_name": element.get("name"),
            "element_role": element.get("role"),
        },
    }
    for index, step in enumerate(steps):
        if step.get("action") == "assert":
            return [*steps[:index], click, *steps[index:]]
    return [*steps, click]


def _ensure_observed_fill(steps: list[dict[str, Any]], start: _State | None, text: str = "") -> list[dict[str, Any]]:
    """Ground text-only imported cases to an editable control from the map.

    Excel imports often describe a validation path in prose (for example,
    "leave username blank") rather than supplying a structured fill action.
    The observed map is authoritative: only an actual editable element is used.
    """
    if any(step.get("action") in {"fill", "select"} for step in steps) or start is None:
        return steps
    decision = match_observed_element(start.state_code, start.elements, {}, "fill", text)
    element = next((item for item in start.elements if item.get("element_code") == decision.element_code), None) if decision.status == "resolved" else None
    if element is None:
        return steps
    fill = {
        "action": "fill",
        "target": {
            "state_code": start.state_code,
            "element_code": element["element_code"],
            "element_name": element.get("name"),
            "element_role": element.get("role"),
        },
        "value": "" if re.search(r"\b(empty|blank|missing|required)\b", text, re.I) else "{negative_input}",
    }
    for index, step in enumerate(steps):
        if step.get("action") in {"click", "assert"}:
            return [*steps[:index], fill, *steps[index:]]
    return [*steps, fill]


def _fill_value(step: dict[str, Any], data: dict[str, Any]) -> str:
    raw = str(step.get("value") or "")
    match = re.fullmatch(r"\{([^{}]+)\}", raw.strip())
    if not match:
        return raw
    key = match.group(1)
    value = _configured_value(data, key)
    return value if isinstance(value, str) else ""


def _configured_value(data, key):
    if key in data:
        return data[key]
    value = data
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value

def _looks_positive(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    return text.casefold() in _POSITIVE_VALUES or ("@" in text and "example" in text.casefold())


def _ensure_negative(
    case: _Case,
    steps: list[dict[str, Any]],
    data: dict[str, Any],
    states: dict[str, _State],
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    fills = [step for step in steps if step.get("action") == "fill"]
    is_disc = lambda s: str(s.get("value") or "").startswith("{discovery:")
    if not fills:
        return data, steps, f"{case.tc_code} is a negative case but does not change an input."
    for step in fills:
        if is_disc(step):
            continue
        placeholder = re.fullmatch(r"\{([^{}]+)\}", str(step.get("value") or ""))
        if placeholder and _configured_value(data, placeholder.group(1)) is None:
            return data, steps, f"Negative input {placeholder.group(1)!r} has no configured fixture."
    if any(not _looks_positive(_fill_value(step, data)) for step in fills if not is_disc(step)):
        return data, steps, None
    return data, steps, "Negative scenario has only positive fixtures; configure the intended invalid value."


def _empty_required(
    steps: list[dict[str, Any]],
    data: dict[str, Any],
    text: str = "",
    states: dict[str, _State] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fills = [step for step in steps if step.get("action") == "fill"]
    if not fills:
        return data, steps
    # Preserve an explicitly empty field; do not clear the first companion field.
    empty = [step for step in fills if step.get("value") == ""]
    if empty:
        return data, steps
    named = [step for step in fills if _step_name(step, states or {}) and
             _step_name(step, states or {}).casefold() in text.casefold()]
    if len(named) != 1:
        return data, steps
    target_step = named[0]
    raw = str(target_step.get("value") or "")
    match = re.fullmatch(r"\{([^{}]+)\}", raw.strip())
    data = dict(data)
    if match:
        data[match.group(1)] = ""
    else:
        target_step["value"] = ""
    return data, steps


def _captcha_without_value(
    case: _Case,
    steps: list[dict[str, Any]],
    data: dict[str, Any],
    states: dict[str, _State],
) -> str | None:
    if _captcha_is_under_test(case, steps, states):
        return None
    for step in steps:
        if step.get("action") != "fill" or not _CAPTCHA.search(_step_name(step, states)):
            continue
        if _fill_value(step, data).strip():
            continue
        if str(data.get("TEST_CAPTCHA") or "").strip():
            continue
        return "CAPTCHA cannot be answered automatically. Set TEST_CAPTCHA for this test environment."
    return None


def _captcha_is_under_test(
    case: _Case,
    steps: list[dict[str, Any]],
    states: dict[str, _State],
) -> bool:
    captcha_fills = [
        step
        for step in steps
        if step.get("action") == "fill" and _CAPTCHA.search(_step_name(step, states))
    ]
    if not captcha_fills:
        return False
    text = _objective(case)
    return bool(
        _CAPTCHA.search(text)
        and (
            (case.category or "").upper() == "NEGATIVE"
            or re.search(r"\b(blank|empty|missing|required|invalid|incorrect|wrong|reject|error|validation)\b", text, re.I)
        )
    )


def _destructive(steps: list[dict[str, Any]], states: dict[str, _State]) -> bool:
    return any(((_element(step, states) or {}).get("risk") == "DESTRUCTIVE") for step in steps)
