"""Choose a starting state and a minimal step list before Playwright is emitted.

Classification uses preconditions first, then the discovered start state, then
the case wording. It does not key off test codes or a particular application.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

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


def prepare_execution(case: _Case, states: dict[str, _State]) -> ExecutionPlan:
    """Return the steps and fixture that should be rendered for this case."""
    steps = _copy_steps(case.steps)
    data = dict(case.test_data or {})
    starting = classify_start(case, states)
    text = _objective(case)
    start = _start_state(case, states)
    destructive = _destructive(steps, states)
    kind = _primary_kind(text, case.category, start, steps, states)

    if kind in {"navigate", "refresh"}:
        steps = [step for step in steps if not _unrelated_fill(step, states, text, keep_credentials=False)]
        steps = _ensure_click(steps, start, states, _click_pattern(kind, text))
    elif kind == "validation":
        steps = [step for step in steps if not _unrelated_fill(step, states, text, keep_credentials=True)]
        steps = _ensure_click(steps, start, states, _SUBMIT)
        if not any(step.get("action") == "fill" for step in steps):
            return _blocked(
                starting,
                steps,
                data,
                f"{case.tc_code} is a validation case but does not change an input.",
            )
        data, steps = _empty_required(steps, data)
    elif kind in {"login_success", "login_rejected"}:
        steps = [step for step in steps if not _unrelated_fill(step, states, text, keep_credentials=True)]
        steps = _ensure_click(steps, start, states, _SUBMIT)
        if kind == "login_rejected":
            data, steps, negative_issue = _ensure_negative(case, steps, data, states)
            if negative_issue:
                return _blocked(starting, steps, data, negative_issue)
    elif _is_negative(case, text):
        data, steps, negative_issue = _ensure_negative(case, steps, data, states)
        if negative_issue:
            return _blocked(starting, steps, data, negative_issue)

    captcha_issue = _captcha_without_value(steps, data, states)
    if captcha_issue:
        return _blocked(starting, steps, data, captcha_issue)

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


def classify_start(case: _Case, states: dict[str, _State]) -> str:
    preconditions = " ".join(case.preconditions or [])
    if _AUTH_PRE.search(preconditions) and not _PUBLIC_PRE.search(preconditions):
        return "AUTHENTICATED"
    start = _start_state(case, states)
    if _PUBLIC_PRE.search(preconditions):
        if start is not None and _credential_surface(start):
            return "LOGIN_PAGE"
        return "PUBLIC"
    if start is not None and _credential_surface(start) and _exercises_auth_surface(case, states):
        return "LOGIN_PAGE"
    if start is not None and not _credential_surface(start):
        if _only_opens_entry(case):
            return "PUBLIC"
        return "AUTHENTICATED"
    text = _objective(case)
    if _credential_language(text) and not _protected_language(text):
        return "LOGIN_PAGE"
    if re.search(r"\b(landing|public|home page)\b", text, re.I):
        return "PUBLIC"
    return "AUTHENTICATED"


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
    return actions.count("click") == 0 and actions.count("fill") == 0


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
    if str(step.get("action") or "") != "fill":
        return False
    name = _step_name(step, states)
    credential = bool(_USERNAME.search(name) or _PASSWORD.search(name) or _CAPTCHA.search(name))
    if credential:
        return not keep_credentials
    return not (name and len(name) >= 3 and name.casefold() in text.casefold())


def _has_click(steps: list[dict[str, Any]], states: dict[str, _State], pattern: re.Pattern[str]) -> bool:
    return any(step.get("action") == "click" and pattern.search(_step_name(step, states)) for step in steps)


def _ensure_click(
    steps: list[dict[str, Any]],
    start: _State | None,
    states: dict[str, _State],
    pattern: re.Pattern[str],
) -> list[dict[str, Any]]:
    if start is None or _has_click(steps, states, pattern):
        return steps
    element = next(
        (
            item
            for item in start.elements
            if item.get("element_code")
            and item.get("risk") != "DESTRUCTIVE"
            and pattern.search(str(item.get("name") or ""))
        ),
        None,
    )
    if element is None:
        return steps
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


def _fill_value(step: dict[str, Any], data: dict[str, Any]) -> str:
    raw = str(step.get("value") or "")
    match = re.fullmatch(r"\{([^{}]+)\}", raw.strip())
    if not match:
        return raw
    key = match.group(1)
    value = data.get(key)
    return value if isinstance(value, str) else ""


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
    if not fills:
        return data, steps, f"{case.tc_code} is a negative case but does not change an input."
    if any(not _looks_positive(_fill_value(step, data)) for step in fills):
        return data, steps, None
    target_step = next(
        (step for step in fills if _PASSWORD.search(_step_name(step, states)) or _CAPTCHA.search(_step_name(step, states))),
        fills[0],
    )
    name = _step_name(target_step, states)
    replacement = "invalid-captcha" if _CAPTCHA.search(name) else "wrong"
    raw = str(target_step.get("value") or "")
    match = re.fullmatch(r"\{([^{}]+)\}", raw.strip())
    if match:
        data = dict(data)
        data[match.group(1)] = replacement
    else:
        target_step["value"] = replacement
    return data, steps, None


def _empty_required(
    steps: list[dict[str, Any]],
    data: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fills = [step for step in steps if step.get("action") == "fill"]
    if not fills:
        return data, steps
    target_step = fills[0]
    raw = str(target_step.get("value") or "")
    match = re.fullmatch(r"\{([^{}]+)\}", raw.strip())
    data = dict(data)
    if match:
        data[match.group(1)] = ""
    else:
        target_step["value"] = ""
    return data, steps


def _captcha_without_value(
    steps: list[dict[str, Any]],
    data: dict[str, Any],
    states: dict[str, _State],
) -> str | None:
    for step in steps:
        if step.get("action") != "fill" or not _CAPTCHA.search(_step_name(step, states)):
            continue
        if _fill_value(step, data).strip():
            continue
        if str(data.get("TEST_CAPTCHA") or "").strip():
            continue
        return "CAPTCHA cannot be answered automatically. Set TEST_CAPTCHA for this test environment."
    return None


def _destructive(steps: list[dict[str, Any]], states: dict[str, _State]) -> bool:
    return any(((_element(step, states) or {}).get("risk") == "DESTRUCTIVE") for step in steps)
