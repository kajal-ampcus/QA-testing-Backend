"""Requirement-derived obligations, evidence-backed drafts and measured coverage.

The existing category enum stays unchanged: security/accessibility/functional
are coverage dimensions, not new database categories. Unsupported obligations
remain in the denominator instead of becoming guessed tests.
"""

import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any

from core.agents.test_design.fast_generate import (
    _build_case,
    _default_categories,
    _observed,
    _rank_states,
    _state_score,
    _target,
    _url_path,
)
from core.agents.test_design.form_behaviors import form_behavior_cases
from core.agents.test_design.schemas import TestCaseSpec, TestStep
from domain.test_case_identity import case_signature

_STOP = {"the", "and", "for", "with", "from", "user", "users", "can", "must", "should", "page", "field", "fields", "input", "inputs", "enter", "your", "are", "is", "be"}
_PRESENCE = re.compile(r"\b(displays?|displayed|visible|available|shows?|shown|present|contains?)\b", re.I)
_REQUIRED = re.compile(r"\b(required|mandatory|missing|blank|empty)\b|\bmust\s+be\s+(entered|provided|filled|supplied)\b", re.I)
_DIMENSIONS = {
    "security": r"\b(security|unauthenticated|unauthorized|unauthorised|permission|protected|masked|token|expired|password reset)\b",
    "accessibility": r"\b(accessib\w*|keyboard|screen reader|accessible name|focus|tab order)\b",
    "boundary": r"\b(boundary|minimum|maximum|min|max|length|limit)\b",
    "negative": r"\b(invalid|incorrect|wrong|reject\w*|missing|mandatory|required|error)\b",
    "edge": r"\b(whitespace|special character|unicode|empty|expired|reuse)\b",
}


def terms(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in _STOP and len(word) > 2}


def clauses(text: str) -> list[str]:
    """Split independent clauses, without splitting field lists or credentials."""
    parts = re.split(r"[;\n]+|(?<=[.!?])\s+|\s+and\s+(?=(?:the\s+)?(?:system|users?|it)\b)", text.strip(), flags=re.I)
    expanded = []
    predicates = r"(?:is|are|has|have|must|should|can|will|cannot|does|allows?|displays?|shows?|redirects?|rejects?|sends?|receives?|expires?|requires?|supports?|accepts?)"
    for part in parts:
        independent = re.split(rf"\s+and\s+(?={predicates}\b)", part, flags=re.I)
        subject = re.match(rf"(.+?)\s+{predicates}\b", independent[0], re.I)
        expanded.extend([independent[0], *[(subject.group(1) + " " + item) if subject else item for item in independent[1:]]])
    return list(dict.fromkeys(part.strip(" .;") for part in expanded if part.strip(" .;")))


def dimension(text: str) -> str:
    return next((name for name, pattern in _DIMENSIONS.items() if re.search(pattern, text, re.I)), "functional")


def _needs_mailbox(text: str) -> bool:
    return bool(re.search(r"\b(email|mailbox|inbox)\b", text, re.I) and
                re.search(r"\b(receiv\w*|deliver\w*|inbox|mailbox)\b|\bsend\w*\b.*\bvia email\b", text, re.I))


@dataclass
class Scenario:
    key: str
    ac_id: str
    description: str
    dimension: str
    category: str
    spec: TestCaseSpec | None = None
    reason: str = "No observed action and measurable result support this behavior."


@dataclass
class ScenarioPlan:
    scenarios: list[Scenario]
    states: list[dict[str, Any]]

    @property
    def cases(self) -> list[TestCaseSpec]:
        unique: dict[tuple[Any, ...], TestCaseSpec] = {}
        for scenario in self.scenarios:
            if scenario.spec is None:
                continue
            signature = case_signature(scenario.spec.model_dump(mode="json"), self.states)
            if signature in unique:
                original = unique[signature]
                original.traceability = sorted(set(original.traceability + scenario.spec.traceability))
            else:
                unique[signature] = scenario.spec.model_copy(deep=True)
        # One form-load smoke can verify several display requirements. Keep
        # each atomic obligation in the coverage plan, not as repetitive cases.
        grouped: dict[str, list[TestCaseSpec]] = {}
        retained = []
        for case in unique.values():
            code = case.steps[0].target.state_code
            state = next((item for item in self.states if item["state_code"] == code), None)
            smoke = state and re.search(r"/(login|sign-in|signin)/?$", _url_path(state), re.I)
            if smoke and all(step.action in {"navigate", "assert"} for step in case.steps) and all(
                (step.assertion or {}).get("kind") == "visible" for step in case.steps if step.action == "assert"):
                grouped.setdefault(code, []).append(case)
            else:
                retained.append(case)
        for cases in grouped.values():
            if len(cases) == 1:
                retained.extend(cases)
                continue
            smoke = cases[0].model_copy(deep=True)
            checks = {case_signature({"steps": [step.model_dump()]}, self.states): step
                      for case in cases for step in case.steps if step.action == "assert"}
            state = next(item for item in self.states if item["state_code"] == smoke.steps[0].target.state_code)
            for element in _observed(state):
                if element.get("role") in {"heading", "button"} and re.fullmatch(r"log\s*in|sign\s*in", str(element.get("name") or ""), re.I):
                    step = TestStep(step_number=1, action="assert", target=_target(state, element),
                        expected=f"{element['name']} {_role_name(element)} is visible",
                        assertion={"kind": "visible", "target": element["element_code"]})
                    checks[case_signature({"steps": [step.model_dump()]}, self.states)] = step
            smoke.steps = [smoke.steps[0], *checks.values()]
            for index, step in enumerate(smoke.steps, 1):
                step.step_number = index
            smoke.title = "Login form loads with the required controls"
            smoke.objective = "Verify the requirement-mapped login controls are displayed; this does not test authentication."
            smoke.expected_result = "; ".join(step.expected for step in smoke.steps if step.action == "assert")
            smoke.traceability = sorted({ac for case in cases for ac in case.traceability})
            retained.append(smoke)
        return retained


def _field_matches(element: dict[str, Any], text: str) -> bool:
    name = str(element.get("name") or "")
    name_terms = terms(name) - {"registered", "address"}
    return bool(name_terms) and name_terms.issubset(terms(text))


def _presence_subject(text: str, category: str) -> str | None:
    if category != "POSITIVE" or not _PRESENCE.search(text) or re.search(
        r"\b(invalid|incorrect|reject\w*|prevent\w*|error|message|click\w*|refresh\w*|generat\w*|add\w*|remov\w*|plac\w*|submit\w*|select\w*|purchas\w*|after|before)\b", text, re.I
    ):
        return None
    # A page name in contextual prose is not itself a requested control.
    after = re.search(r"\b(?:displays?|shows?|contains?)\s+(?:fields?\s+for\s+)?(.+)", text, re.I)
    if after:
        return after.group(1)
    before = re.split(r"\b(?:is|are)\s+(?:displayed|visible|available|shown|present)\b", text, maxsplit=1, flags=re.I)
    return before[0] if len(before) == 2 else text


def _role_name(element: dict[str, Any]) -> str:
    role = str(element.get("role") or "control").lower()
    return {"textbox": "input", "searchbox": "input", "spinbutton": "input", "combobox": "dropdown"}.get(role, role)


def _page_context(text: str) -> tuple[str | None, str]:
    match = re.search(r"\b(?:on|from|in)\s+(?:the\s+)?([\w -]+?)\s+(?:page|screen|form)\b", text, re.I)
    if not match:
        return None, text
    return match.group(1).strip(), text[:match.start()] + text[match.end():]


def _on_page(state: dict, page: str) -> bool:
    leaf = _url_path(state).strip('/').split('/')[-1].lower()
    label = re.sub(r'[^a-z0-9]', '', page.lower())
    if label in {'home', 'homepage'}:
        return not leaf or bool(re.fullmatch(r'[a-z]{2}', leaf))
    return bool(leaf) and label.endswith(re.sub(r'[^a-z0-9]', '', leaf))


def _spec(ac_id: str, state: dict, element: dict, title: str, expected: str,
          assertion: dict[str, str], category: str = "POSITIVE", value: str | None = None) -> TestCaseSpec:
    steps = [TestStep(step_number=1, action="navigate", target=_target(state))]
    if value is not None:
        steps.append(TestStep(step_number=2, action="fill", target=_target(state, element), value="{field_value}"))
    steps.append(TestStep(step_number=len(steps) + 1, action="assert", target=_target(state, element),
                          expected=expected, assertion={**assertion, "target": element["element_code"]}))
    return TestCaseSpec(title=title[:300], objective=expected, category=category,
                        preconditions=[f"Browser is on {_url_path(state)}"], steps=steps,
                        test_data={"field_value": value} if value is not None else {},
                        expected_result=expected, traceability=[ac_id], confidence=0.9)


def plan_scenarios(acceptance_criteria: list[dict[str, Any]], states: list[dict[str, Any]],
                   title: str, description: str = "",
                   requested_categories: dict[str, set[str]] | None = None,
                   starting_states: set[str] | None = None) -> ScenarioPlan:
    scenarios: list[Scenario] = []
    criteria = list(acceptance_criteria)
    # Supplement ACs with explicit requirement rules only when the same named
    # observed field anchors both. A broad feature summary is not a new rule.
    for rule in clauses(str(description or "")):
        if not re.search(r"\b(required|mandatory|masked|minimum|maximum|invalid|accessible name)\b", rule, re.I):
            continue
        eligible = [ac for ac in acceptance_criteria if any(
            _field_matches(element, rule) and _field_matches(element, str(ac.get("text") or ""))
            for state in states for element in _observed(state))]
        if not eligible:
            continue
        selected = max(eligible, key=lambda ac: len(terms(rule) & terms(str(ac.get("text") or ""))))
        if terms(rule).issubset(terms(str(selected.get("text") or ""))):
            continue
        criteria.append({**selected, "text": rule})
    for criterion_index, ac in enumerate(criteria):
        for clause_index, text in enumerate(clauses(str(ac.get("text") or "")) or [""]):
            ac_id = str(ac["id"])
            category = _default_categories({"text": text})[0]
            prefix = f"{ac_id}:{criterion_index}:{clause_index}"
            if _needs_mailbox(text):
                scenarios.append(Scenario(f"{prefix}:mailbox", ac_id, text, "functional", category,
                    reason="Email delivery requires test-mailbox evidence; a browser confirmation alone is insufficient."))
                continue
            sources = [state for state in states if starting_states is None or state["state_code"] in starting_states]
            page_context, control_text = _page_context(text)
            if page_context:
                sources = [state for state in sources if _on_page(state, page_context)]
            ranked = _rank_states(sources, {"text": text}, title) if sources else []
            # Shared labels (especially CAPTCHA) do not move a login criterion
            # onto recovery merely because that page contains more prose.
            field_sources = [state for state in ranked if any(
                element.get('role') in {'textbox', 'searchbox', 'spinbutton', 'combobox'} and _field_matches(element, control_text)
                for element in _observed(state))]
            described_page, _ = _page_context(description)
            if not page_context and described_page and (field_sources or (
                _REQUIRED.search(text) and re.search(r'\b(fields?|inputs?)\b', text, re.I)
            )) and not re.search(r'\b(click\w*|redirect\w*|navigate\w*)\b', text, re.I):
                described_sources = [state for state in ranked if _on_page(state, described_page)]
                if described_sources:
                    ranked = described_sources
                    field_sources = [state for state in field_sources if state in described_sources]
            if not page_context and field_sources and not re.search(r'\b(click\w*|redirect\w*|navigate\w*)\b', text, re.I):
                ranked = sorted(field_sources, key=lambda state: len(
                    set(_url_path(state).strip('/').split('/')[-1].split('-')) & terms(title)), reverse=True)
            if not page_context and _REQUIRED.search(text) and re.search(r'\b(fields?|inputs?)\b', text, re.I):
                required_sources = [state for state in ranked if any(
                    (element.get('validation') or {}).get('required') or element.get('required') for element in _observed(state))]
                if required_sources:
                    ranked = sorted(required_sources, key=lambda state: len(
                        set(_url_path(state).strip('/').split('/')[-1].split('-')) & terms(title)), reverse=True)
            # A requirement title is context, not evidence that every page belongs
            # to every criterion. Never walk unrelated pages until something fits.
            relevant = [state for state in ranked if _state_score(state, terms(text)) > 0]
            if re.search(r"\b(click\w*|send\w*|save\w*|open\w*|refresh\w*|redirect\w*|navigate\w*)\b", text, re.I):
                sources = [state for state in ranked if any(
                    element.get("role") in {"button", "link"} and _field_matches(element, text)
                    for element in _observed(state))]
                if sources:
                    relevant = sources
            if re.search(r"\b(authenticat\w*|credentials|log\s*in|sign\s*in)\b", text, re.I):
                login_sources = [state for state in ranked if any(
                    element.get("role") == "button" and re.fullmatch(r"log\s*in|sign\s*in", str(element.get("name") or ""), re.I)
                    for element in _observed(state))]
                if login_sources:
                    relevant = login_sources
                elif re.search(r"\b(successful\w*|valid)\b", text, re.I):
                    # A page reached after authentication is result evidence,
                    # not a starting state that can prove authentication.
                    relevant = []
            entry_navigation = bool(re.search(r"\b(click\w*|open\w*|navigate\w*|redirect\w*)\b", text, re.I) and
                                    re.search(r"\blogin\s+(page|form|screen)\b", text, re.I) and
                                    not re.search(r"\bforgot\s+password\b", text, re.I) and
                                    not re.search(r"\b(valid credentials|successful authentication)\b", text, re.I))
            if entry_navigation:
                relevant = [state for state in ranked if any(
                    element.get("role") == "link" and re.search(r"/(login|sign-in|signin)/?(?:[?#].*)?$", str(element.get("url") or ""), re.I)
                    for element in _observed(state))]
                relevant.sort(key=lambda state: (len(state.get('reached_via') or []), len(_url_path(state))))
            generic_form_words = {'all', 'any', 'each', 'every', 'required', 'mandatory', 'missing', 'blank', 'empty',
                'display', 'displays', 'trigger', 'triggers', 'appropriate', 'validation', 'validations', 'message',
                'messages', 'error', 'errors', 'form', 'submission', 'submit', 'submitting', 'blocked', 'blocks',
                'reject', 'rejected', 'prevents', 'prevent', 'when', 'are', 'not', 'provided', 'filled'}
            if not relevant and _REQUIRED.search(text) and not (terms(text) - generic_form_words):
                relevant = [state for state in ranked if _state_score(state, terms(title)) > 0 and any(
                    (element.get("validation") or {}).get("required") or element.get("required") for element in _observed(state))]
            state = relevant[0] if relevant else None
            elements = _observed(state) if state else []
            matched = [element for element in elements if _field_matches(element, control_text)]
            presence_subject = _presence_subject(control_text, category)
            explicit_role = re.search(r'\b(heading|button|link|textbox|input|dropdown)\b', presence_subject or '', re.I)
            if explicit_role:
                roles = {'input': {'textbox', 'searchbox', 'spinbutton'}, 'dropdown': {'combobox'}}
                matched = [element for element in matched if element.get('role') in roles.get(explicit_role.group(1).lower(), {explicit_role.group(1).lower()})]
            if _REQUIRED.search(text) and (re.search(r"\b(fields|inputs)\b", text, re.I) or (not matched and re.search(r"\b(field|input)\b", text, re.I))):
                matched = [element for element in elements if (element.get("validation") or {}).get("required") or element.get("required")]
            made: list[Scenario] = []

            def add(suffix: str, label: str, spec: TestCaseSpec | None, kind: str | None = None,
                    test_category: str | None = None, reason: str | None = None,
                    prefix: str = prefix, ac_id: str = ac_id, text: str = text,
                    category: str = category, made: list[Scenario] = made) -> None:
                scenario = Scenario(f"{prefix}:{suffix}", ac_id, label, kind or dimension(text),
                                    test_category or (spec.category if spec else category), spec)
                if reason:
                    scenario.reason = reason
                made.append(scenario)

            for element in matched:
                name = str(element.get("name") or element["element_code"])
                code = str(element["element_code"])
                validation = element.get("validation") or {}
                input_type = str(element.get("type") or element.get("input_type") or "").lower()
                fillable = element.get("role") in {"textbox", "searchbox", "spinbutton", "combobox"}
                if presence_subject and element.get("role") not in {"RootWebArea", "WebArea", "generic", "form"} and _field_matches(element, presence_subject):
                    expected = f"{name} is visible"
                    add(f"{code}:visible", expected, _spec(ac_id, state, element, f"{name} {_role_name(element)} is visible", expected, {"kind": "visible"}))
                if fillable and element.get("role") != "combobox" and _REQUIRED.search(text) and (validation.get("required") or element.get("required")):
                    for value, missing, label, cat in [("", "true", "empty required input", "NEGATIVE"),
                                                        ("0" if input_type == "number" else "a", "false", "one-character required boundary", "EDGE_CASE")]:
                        expected = f"{name}: native valueMissing is {missing}; no claim about other validation rules"
                        add(f"{code}:required:{missing}", label,
                            _spec(ac_id, state, element, f"{name}: {label}", expected,
                                  {"kind": "validity", "property": "valueMissing", "value": missing}, cat, value),
                            "negative" if missing == "true" else "boundary")
                if re.search(r"\b(masked|masking)\b", text, re.I) and fillable:
                    expected = f"{name} uses the observed password input type"
                    spec = _spec(ac_id, state, element, expected, expected,
                                 {"kind": "attribute", "name": "type", "value": "password"}) if input_type == "password" else None
                    add(f"{code}:masked", expected, spec, "security")
                if re.search(r"\baccessible name\b", text, re.I) and name:
                    expected = f"{name} has its observed accessible name"
                    add(f"{code}:accessible-name", expected, _spec(ac_id, state, element, expected, expected,
                            {"kind": "accessible_name", "text": name}), "accessibility")
                if fillable and input_type == "email" and re.search(r"\b(invalid|malformed|format)\b", text, re.I):
                    expected = f"{name}: native typeMismatch is true for a malformed email"
                    add(f"{code}:email-format", expected,
                        _spec(ac_id, state, element, expected, expected,
                              {"kind": "validity", "property": "typeMismatch", "value": "true"}, "NEGATIVE", "not-an-email"), "negative")
                if fillable and re.search(r"\b(length|boundary|minimum|maximum)\b", text, re.I):
                    for attr in ("minlength", "maxlength"):
                        limit = validation.get(attr, element.get(attr))
                        if limit is None:
                            continue
                        expected = f"{name} has the observed {attr} constraint {limit}"
                        add(f"{code}:{attr}", expected, _spec(ac_id, state, element, expected, expected,
                            {"kind": "attribute", "name": attr, "value": str(limit)}), "boundary")
                        # Native maxlength can truncate inserted text. Do not
                        # invent a rejection/error scenario from the attribute.
                        if attr == "maxlength":
                            add(f"{code}:{attr}:behavior", f"Verify {name} maximum-length input handling", None, "boundary", "EDGE_CASE",
                                "The limit is observed, but truncation versus application rejection needs interaction evidence.")
                        elif str(limit).isdigit() and 0 < int(limit) <= 10000:
                            for size in (int(limit) - 1, int(limit), int(limit) + 1):
                                if size == 0:
                                    continue  # empty optional inputs do not fail minlength
                                expected_flag = "true" if size < int(limit) else "false"
                                expected = f"{name}: native tooShort is {expected_flag} for {size} characters"
                                add(f"{code}:tooShort:{size}", expected,
                                    _spec(ac_id, state, element, expected, expected,
                                          {"kind": "validity", "property": "tooShort", "value": expected_flag}, "EDGE_CASE", "a" * size), "boundary")
                # Numeric native constraints supply measurable, rule-specific
                # boundaries. Never assume limits from field names or examples.
                if fillable and re.search(r"\b(boundary|minimum|maximum|min|max|limit|range)\b", text, re.I):
                    for key, prop, sign in [("min", "rangeUnderflow", -1), ("max", "rangeOverflow", 1)]:
                        limit = validation.get(key, element.get(key))
                        if limit is None or input_type != "number":
                            continue
                        try:
                            number = Decimal(str(limit))
                            delta = Decimal(str(validation.get("step", element.get("step", "1"))))
                            if not number.is_finite() or not delta.is_finite() or delta <= 0:
                                continue
                        except InvalidOperation:
                            continue
                        for value, outside in [(number + sign * delta, "true"), (number, "false"), (number - sign * delta, "false")]:
                            literal = format(value, "f")
                            expected = f"{name}: native {prop} is {outside} for {literal}"
                            add(f"{code}:{prop}:{literal}", expected,
                                _spec(ac_id, state, element, expected, expected,
                                      {"kind": "validity", "property": prop, "value": outside}, "EDGE_CASE", literal), "boundary")

            # Structural checks cannot stand in for submitting a form, sending
            # email, changing a password, toggling a control, or authorization.
            action_promised = bool(re.search(r"\b(click\w*|submit\w*|send\w*|reset\w*|refresh\w*|redirect\w*|accepts?|authenticat\w*|messages?|log\s*out|sign\s*out|prevent\w*|reject\w*)\b|\b(log\s*in|sign\s*in)\b(?!\s+(?:page|form|screen)\b)", text, re.I))
            if not made or action_promised:
                spec = None
                if state is not None:
                    spec = _build_case(ac={**ac, "text": text}, category=category,
                                       start=state, states=states, title=text)
                    # An unchanged text/visibility check does not test an action.
                    if spec and action_promised and not any(step.action == "click" for step in spec.steps):
                        spec = None
                    if spec and re.search(r'\bsessions?\b', text, re.I) and not re.search(r'\b(log\s*in|sign\s*in|credentials)\b', text, re.I) and any(
                        step.action == 'click' and re.fullmatch(r'log\s*in|sign\s*in', step.target.element_name or '', re.I) for step in spec.steps
                    ):
                        spec = None  # sign-in alone does not test session continuity
                    if spec and not any(step.action == "click" for step in spec.steps):
                        # Explicit display obligations were matched above using
                        # complete control names. A generic partial-word match
                        # (e.g. "the" inside a person's name) is not an oracle.
                        spec = None
                    if spec and category == "EDGE_CASE":
                        # Generic synthetic values and visibility cannot prove a
                        # limit. Supported numeric boundaries are built above.
                        spec = None
                    if spec and presence_subject and not any(step.action == "click" for step in spec.steps):
                        spec = None  # explicit display checks were handled above
                if spec and spec.steps[-1].assertion.get("kind") == "url" and state and states:
                    result_state = next((item for item in states if item["state_code"] == spec.steps[-1].target.state_code), None)
                    if result_state and result_state.get("url_pattern") == state.get("url_pattern"):
                        spec = None
                if spec:
                    spec.title = text[:300]
                    spec.objective = f"Verify {text} using the observed action and result."
                    if category == "POSITIVE" and any(step.action == "click" and re.fullmatch(r"log\s*in|sign\s*in", step.target.element_name or "", re.I) for step in spec.steps):
                        spec.title = "Sign in with configured valid credentials"
                    if re.search(r"\b(registered|valid credentials|authenticat\w*)\b", text, re.I) or spec.title == "Sign in with configured valid credentials":
                        spec.preconditions.append("Use a configured test account and any required live authentication challenge inputs.")
                add("behavior", text, spec)
            behavior_cases = form_behavior_cases(ac_id, text, state)
            if any(case.steps[-1].assertion['basis'] == 'invalid' for _, case in behavior_cases):
                # A discovered error screen does not bind that outcome to the
                # synthetic inputs of the generic negative template. Replace
                # it with explicit, independently varied invalid fixtures.
                made[:] = [item for item in made if item.key != f'{prefix}:behavior']
            if any(case.steps[-1].assertion['basis'] in {'missing', 'invalid', 'valid', 'refresh'} for _, case in behavior_cases):
                made[:] = [item for item in made if item.spec is not None or item.key != f'{prefix}:behavior']
            for behavior_index, (behavior_dimension, behavior_case) in enumerate(behavior_cases):
                mode = behavior_case.steps[-1].assertion['basis']
                if mode == 'valid' and any(item.spec and any(step.action == 'click' for step in item.spec.steps) for item in made):
                    continue  # an observed result already supports this happy path
                add(f"form-behavior:{behavior_index}", behavior_case.title, behavior_case,
                    behavior_dimension, behavior_case.category)
            # A requested negative/edge repair must not silently disappear just
            # because the AC's primary category is positive.
            wanted = (requested_categories or {}).get(ac_id)
            if wanted:
                made = [item for item in made if item.category in wanted]
                for missing_category in sorted(wanted - {item.category for item in made}):
                    made.append(Scenario(f"{prefix}:requested:{missing_category}", ac_id, text,
                                         dimension(text), missing_category, reason="Requested category has no evidence-backed input and outcome."))
            scenarios.extend(made)
    unique = {}
    for scenario in scenarios:
        identity = (scenario.ac_id, case_signature(scenario.spec.model_dump(mode="json"), states)) if scenario.spec else (scenario.ac_id, scenario.description, scenario.category)
        unique.setdefault(identity, scenario)
    return ScenarioPlan(list(unique.values()), states)


def measure_coverage(plan: ScenarioPlan, cases: list[TestCaseSpec]) -> dict[str, Any]:
    """Design coverage, not execution/pass rate. Unknown obligations stay missing."""
    signatures: dict[str, set[tuple[Any, ...]]] = {}
    for case in cases:
        for ac_id in case.traceability:
            signatures.setdefault(ac_id, set()).add(case_signature(case.model_dump(mode="json"), plan.states))
    rows = []
    for scenario in plan.scenarios:
        expected = case_signature(scenario.spec.model_dump(mode="json"), plan.states) if scenario.spec else None
        covered = expected is not None and any(_covers(expected, saved) for saved in signatures.get(scenario.ac_id, set()))
        rows.append({"scenario_id": scenario.key, "ac_id": scenario.ac_id, "dimension": scenario.dimension,
                     "verification": "manual" if scenario.spec and any((step.assertion or {}).get('kind') == 'manual' for step in scenario.spec.steps) else "automated" if scenario.spec else "unresolved",
                     "category": scenario.category, "description": scenario.description,
                     "status": "covered" if covered else "missing",
                     "reason": ("Requirement-backed test saved for manual verification; not an automated pass." if scenario.spec and any((step.assertion or {}).get('kind') == 'manual' for step in scenario.spec.steps) else "Validated executable design scenario saved.") if covered else
                     "No matching validated saved case; review validation issues and current evidence." if scenario.spec else scenario.reason})
    covered = sum(row["status"] == "covered" for row in rows)
    manual_covered = sum(row['status'] == 'covered' and row['verification'] == 'manual' for row in rows)
    total = len(rows)
    ac_ids = {row["ac_id"] for row in rows}
    complete_acs = sum(all(row["status"] == "covered" for row in rows if row["ac_id"] == ac_id) for ac_id in ac_ids)
    by_dimension = {}
    for name in sorted({row["dimension"] for row in rows}):
        selected = [row for row in rows if row["dimension"] == name]
        passed = sum(row["status"] == "covered" for row in selected)
        by_dimension[name] = {"total": len(selected), "covered": passed, "missing": len(selected) - passed,
                              "coverage_percent": round(100 * passed / len(selected), 2)}
    return {"basis": "validated requirement-derived design scenarios; not execution coverage",
            "manual_scenarios": manual_covered, "automatable_scenarios": covered - manual_covered,
            "automatable_design_coverage_percent": round(100 * (covered - manual_covered) / total, 2) if total else None,
            "total_scenarios": total, "covered_scenarios": covered, "missing_scenarios": total - covered,
            "scenario_coverage_percent": round(100 * covered / total, 2) if total else None,
            "total_acs": len(ac_ids), "fully_covered_acs": complete_acs,
            "ac_coverage_percent": round(100 * complete_acs / len(ac_ids), 2) if ac_ids else None,
            "by_dimension": by_dimension, "scenarios": rows}


def _covers(expected: tuple, actual: tuple) -> bool:
    required, saved = expected[0], actual[0]
    # Additional assertions can share one smoke case. Additional interactions
    # change the behavior and must not be mistaken for a display-only check.
    return ([step for step in required if step[0] != "assert"] == [step for step in saved if step[0] != "assert"] and
            {step for step in required if step[0] == "assert"}.issubset({step for step in saved if step[0] == "assert"}))


def bind_supported_cases(plan: ScenarioPlan, cases: list[TestCaseSpec]) -> None:
    """Recognize validated model/manual alternatives without trusting AC tags alone."""
    from core.agents.test_design.validation import validate_case

    for scenario in plan.scenarios:
        if scenario.spec is not None:
            continue
        if _needs_mailbox(scenario.description):
            continue
        for case in cases:
            if scenario.ac_id not in case.traceability or case.category != scenario.category:
                continue
            if validate_case(case, plan.states, {item.ac_id for item in plan.scenarios},
                             {scenario.ac_id: scenario.description}, strict=True):
                continue
            controls = [step for step in case.steps if step.action in {"click", "fill", "select"}]
            assertions = [step for step in case.steps if step.action == "assert"]
            mapped_text = " ".join(str(step.target.element_name or "") + " " + str((step.assertion or {}).get("text") or "") for step in case.steps)
            if not (terms(mapped_text) & terms(scenario.description)):
                continue
            # Inventory-only checks cannot discharge behavior/validation promises.
            if not controls and not any((step.assertion or {}).get("kind") in {"attribute", "accessible_name"} for step in assertions):
                continue
            if re.search(r"\b(click\w*|submit\w*|send\w*|receiv\w*|request\w*|reset\w*|refresh\w*|redirect\w*|authentication|log\s*in|sign\s*in)\b", scenario.description, re.I) and not any(step.action == "click" for step in controls):
                continue
            if scenario.dimension == "accessibility" and not any((step.assertion or {}).get("kind") == "accessible_name" for step in assertions):
                continue
            if scenario.category in {"NEGATIVE", "EDGE_CASE"} and not any(
                (step.assertion or {}).get("kind") in {"validity", "invalid", "required", "text"} for step in assertions
            ):
                continue
            scenario.spec = case
            break


def relevant_candidate(plan: ScenarioPlan, case: TestCaseSpec, descriptions: list[str]) -> bool:
    """Only save model candidates that discharge the requested missing task."""
    targets = [scenario for scenario in plan.scenarios if scenario.description in descriptions]
    for scenario in targets:
        if scenario.spec is not None:
            if (scenario.ac_id in case.traceability and
                _covers(case_signature(scenario.spec.model_dump(mode="json"), plan.states), case_signature(case.model_dump(mode="json"), plan.states))):
                return True
        else:
            probe = ScenarioPlan([replace(scenario)], plan.states)
            bind_supported_cases(probe, [case])
            if probe.scenarios[0].spec is not None:
                return True
    return False
