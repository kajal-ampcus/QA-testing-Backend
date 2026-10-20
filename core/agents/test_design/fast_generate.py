"""Map-grounded test cases without waiting on a slow reasoning LLM.

gpt-oss-20b regularly exceeds 120s for even two structured cases, which made
every AC time out. These drafts use only observed states and elements so they
pass deterministic validation and return in seconds.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from core.agents.application_discovery.dropdowns import (
    behavior_groups,
    option_entries,
    representative_option,
)
from core.agents.test_data.agent import value_for_element
from core.agents.test_design.schemas import StepTarget, TestCaseSpec, TestStep

_FILLABLE = {"textbox", "searchbox", "spinbutton", "combobox"}
_CLICKABLE = {"button", "link", "checkbox", "radio"}
_COVERABLE = _FILLABLE | _CLICKABLE
_SUBMIT = re.compile(r"\b(submit|send|save|log.?in|sign.?in|continue|next)\b", re.I)
_CONTROL_LABEL = re.compile(r"observed_link='([^']*)'|name='([^']*)'", re.I)
_CLICK_NAME = re.compile(r"^click\s+([^()]+)$", re.I)
_NEGATIVE_INTENT = re.compile(
    r"\b(invalid|incorrect|missing|required|reject|prevent|deny|denied|unauthori[sz]ed|"
    r"must not|cannot|can't|does not|do not|without|error|fail|closed|expired|cancelled)\b",
    re.I,
)
_EDGE_INTENT = re.compile(
    r"\b(boundary|minimum|maximum|limit|zero|empty|special characters?|length|"
    r"first|last|cutoff|month end|leap|overflow|negative amount|negative count)\b",
    re.I,
)


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
    """Rank a page using this criterion, not the requirement's broad title.

    A requirement commonly describes several pages (for example Login, Forgot
    Password, and Dashboard).  Including that title in every criterion made
    those page names equally relevant, so a shared field such as Employee ID
    could be drafted against the Forgot Password page for a login criterion.
    """
    criterion = str(ac.get("text") or "")
    terms = {
        word.lower()
        for word in re.findall(r"[a-zA-Z0-9]{3,}", criterion)
    }
    scored = [(state, _state_score(state, terms)) for state in states]
    # A terse AC may not contain any visible page/control words. In that
    # limited case, the requirement title remains a fallback rather than a
    # signal that every page belongs to every AC.
    if not any(score for _, score in scored):
        fallback_terms = {
            word.lower() for word in re.findall(r"[a-zA-Z0-9]{3,}", title)
        }
        scored = [(state, _state_score(state, fallback_terms)) for state in states]
    return [state for state, _ in sorted(scored, key=lambda item: item[1], reverse=True)]


def _url_path(state: dict[str, Any]) -> str:
    pattern = str(state.get("url_pattern") or "/")
    match = re.search(r"/[A-Za-z0-9_/-]*", pattern)
    return match.group(0) if match else "/"


_CHROME = re.compile(r"\b(toggle theme|dark mode|light mode)\b", re.I)


def _best_click(elements: list[dict[str, Any]], text: str, *, criterion: str | None = None) -> dict[str, Any] | None:
    """Pick a control the case is about.

    Theme switches are chrome. They are not the action for menu, order,
    notification, or logout cases, even when they are the first button.
    """
    clicks = [element for element in elements if _usable(element, _CLICKABLE)]
    pool = [element for element in clicks if not _CHROME.search(str(element.get("name") or ""))]
    if not pool:
        return None
    intent = criterion if criterion is not None else text
    if (
        re.search(r"\b(authentication|credentials|log\s*in|sign\s*in)\b", intent, re.I)
        and re.search(r"\b(successful|successfully|valid)\b", intent, re.I)
        and not _NEGATIVE_INTENT.search(intent)
    ):
        login = next((element for element in pool if re.fullmatch(
            r"log\s*in|sign\s*in", str(element.get("name") or "").strip(), re.I
        )), None)
        if login is not None:
            return login
    terms = {word.lower() for word in re.findall(r"[a-zA-Z0-9]{3,}", text)}

    def score(element: dict[str, Any]) -> int:
        name = str(element.get("name") or "").lower()
        return sum(term in name for term in terms)

    ranked = sorted(pool, key=score, reverse=True)
    if score(ranked[0]) > 0:
        return ranked[0]
    submits = [element for element in pool if _SUBMIT.search(str(element.get("name") or ""))]
    return submits[0] if submits else None


def _best_observed_element(
    elements: list[dict[str, Any]], text: str, *, exclude: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    terms = {word.lower() for word in re.findall(r"[a-zA-Z0-9]{3,}", text)}
    ranked: list[tuple[int, dict[str, Any]]] = []
    for element in elements:
        if element is exclude:
            continue
        name = str(element.get("name") or element.get("text") or "").lower()
        score = sum(term in name for term in terms)
        if score:
            ranked.append((score, element))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def _name_score(element: dict[str, Any], text: str) -> int:
    name = str(element.get("name") or element.get("text") or "").lower()
    terms = [word.lower() for word in re.findall(r"[a-zA-Z0-9]{3,}", text)]
    return sum(term in name for term in terms)


def _result_content_element(
    state: dict[str, Any], text: str
) -> dict[str, Any] | None:
    """A content element on the destination page that proves the outcome.

    A heading is the strongest evidence and matches how a tester writes an
    expected result. A URL check proves navigation only, so it is the last
    resort and only used when the destination page has no readable content.
    """
    observed = _observed(state)
    for roles in ({"heading"}, {"statictext", "paragraph"}):
        candidates = [
            element
            for element in observed
            if str(element.get("role") or "").lower() in roles
            and str(element.get("name") or element.get("text") or "").strip()
        ]
        if candidates:
            return sorted(
                candidates, key=lambda element: _name_score(element, text), reverse=True
            )[0]
    return None


def _content_assertion(
    state: dict[str, Any], text: str
) -> tuple[StepTarget, dict[str, str], str]:
    """Build the assert step target, expected text and structured intent.

    Returns (step_target, assertion, expected).
    """
    content = _result_content_element(state, text)
    if content is None:
        return (
            _target(state),
            {"kind": "url", "target": str(state.get("state_code") or "")},
            f"The current URL includes {_url_path(state)}",
        )
    label = str(content.get("name") or content.get("text") or "").strip()
    code = str(content.get("element_code") or "")
    is_heading = str(content.get("role") or "").lower() == "heading"
    assertion = {
        "kind": "text" if is_heading else "visible",
        "target": code,
    }
    if is_heading:
        assertion["text"] = label
    return _target(state, content), assertion, f"{label} is visible"


def _default_categories(ac: dict[str, Any]) -> list[str]:
    text = str(ac.get("text") or "")
    if _EDGE_INTENT.search(text):
        return ["EDGE_CASE"]
    if _NEGATIVE_INTENT.search(text):
        return ["NEGATIVE"]
    return ["POSITIVE"]


def _route(url: str) -> str:
    text = str(url or "").strip()
    if not text or text.startswith("data:"):
        return ""
    path = urlparse(text).path if "://" in text else text
    return path.rstrip("/") or "/"


def _control_label(step: str) -> str:
    match = _CONTROL_LABEL.search(step)
    if match:
        return next((group for group in match.groups() if group), "")
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
    if re.fullmatch(r"log\s*in|sign\s*in", name):
        # Discovery records browser authentication as a semantic action rather
        # than a literal click on the application's submit-button label.
        prefix = start.get("reached_via") or []
        authenticated = [state for state in states
            if state["state_code"] != start["state_code"]
            and (state.get("reached_via") or [])[:len(prefix)] == prefix
            and re.search(r"role=authentication(?:,|\))", str((state.get("reached_via") or [""])[-1]))]
        if len(authenticated) == 1:
            return authenticated[0], 0.8
    if len(name) >= 2:
        for state in states:
            path = state.get("reached_via") or []
            label = _norm_label(_control_label(str(path[-1]))) if path else ""
            if state["state_code"] != start["state_code"] and label and label == name:
                return state, 0.8
    return start, 0.5


def _dropdown_options(element: dict[str, Any]) -> list[dict[str, Any]]:
    validation = element.get("validation") if isinstance(element.get("validation"), dict) else {}
    raw = validation.get("options") or element.get("options")
    return option_entries(raw)


def _dropdown_labels(element: dict[str, Any], text: str) -> list[str]:
    options = _dropdown_options(element)
    if not options:
        return []
    groups = behavior_groups(list(element.get("dropdown_dependencies") or []))
    distinct = [str(group.get("option") or "") for group in groups if group.get("option")]
    if len(distinct) >= 2:
        return distinct[:2]
    label = representative_option(options, text)
    return [label] if label else []


def _fill_value(element: dict[str, Any], *, category: str) -> tuple[str, str, str]:
    datum = value_for_element(element, category)  # type: ignore[arg-type]
    return datum.key, datum.name, datum.value


def _credential_field(element: dict[str, Any]) -> bool:
    blob = f"{element.get('name') or ''} {element.get('type') or ''}"
    return bool(re.search(r"password|passphrase|e-?mail|username|user name|employee\s*id|staff\s*id|user\s*id|captcha", blob, re.I))


def _named_in_text(element: dict[str, Any], text: str) -> bool:
    name = str(element.get("name") or "").strip()
    return len(name) >= 3 and name.casefold() in text.casefold()


def _relevant_fills(
    elements: list[dict[str, Any]],
    text: str,
    click: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Fill a field only when the objective or the primary action needs it."""
    fills = [element for element in elements if _usable(element, _FILLABLE)]
    click_name = str((click or {}).get("name") or "")
    navigation = bool(click) and (
        str((click or {}).get("role") or "") == "link" or bool((click or {}).get("url"))
    ) and not _SUBMIT.search(click_name)
    if navigation or re.search(r"forgot|refresh", click_name, re.I):
        return [element for element in fills if _named_in_text(element, text) and not _credential_field(element)]
    if click and _SUBMIT.search(click_name):
        return [
            element
            for element in fills
            if _credential_field(element) or _named_in_text(element, text)
        ]
    return [element for element in fills if _named_in_text(element, text)]


def _build_case(
    *,
    ac: dict[str, Any],
    category: str,
    start: dict[str, Any],
    states: list[dict[str, Any]],
    title: str,
    dropdown_variant: int = 0,
) -> TestCaseSpec | None:
    elements = _observed(start)
    text = f"{title} {ac.get('text', '')}"
    if dropdown_variant and not any(
        len(_dropdown_labels(element, text)) > dropdown_variant
        for element in elements
        if element.get("role") == "combobox"
    ):
        return None
    click = _best_click(elements, text, criterion=str(ac.get("text") or ""))
    if (re.search(r"\blog\s*out|\bsign\s*out", str(ac.get("text") or ""), re.I)
        and re.fullmatch(r"log\s*in|sign\s*in", str((click or {}).get("name") or ""), re.I)):
        return None
    fills = _relevant_fills(elements, text, click)
    if category in {"NEGATIVE", "EDGE_CASE"} and not fills:
        return None  # nothing to vary; repeating the positive inputs adds no value
    result, confidence = _result_state(states, start, click)
    valid = category == "POSITIVE"
    steps: list[TestStep] = [
        TestStep(step_number=1, action="navigate", target=_target(start)),
    ]
    test_data: dict[str, str] = {}
    for element in fills:
        labels = _dropdown_labels(element, text) if element.get("role") == "combobox" else []
        if labels and category == "NEGATIVE":
            continue
        if labels:
            chosen = labels[min(dropdown_variant, len(labels) - 1)]
            key = f"option_{element.get('element_code') or 'dropdown'}_{dropdown_variant}"
            test_data[key] = chosen
            steps.append(
                TestStep(
                    step_number=len(steps) + 1,
                    action="select",
                    target=_target(start, element),
                    value=f"{{{key}}}",
                )
            )
            continue
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
    if click:
        steps.append(
            TestStep(
                step_number=len(steps) + 1,
                action="click",
                target=_target(start, click),
            )
        )

    assertion_element = _best_observed_element(elements, text, exclude=click)
    result_text = " ".join(
        [str(result.get("url_pattern") or "")]
        + [str(element.get("name") or "") for element in _observed(result)]
    )
    observed_negative_result = bool(_NEGATIVE_INTENT.search(result_text))
    if result["state_code"] != start["state_code"] and (
        valid or (category == "NEGATIVE" and observed_negative_result)
    ):
        assert_state = result
        assertion_target, assertion, expected = _content_assertion(result, text)
    elif not click and assertion_element:
        assert_state = start
        expected = f"{assertion_element.get('name') or assertion_element.get('text')} is visible"
        assertion_target = _target(assert_state, assertion_element)
        assertion = {
            "kind": "visible",
            "target": str(assertion_element.get("element_code") or ""),
        }
    else:
        return None
    steps.append(
        TestStep(
            step_number=len(steps) + 1,
            action="assert",
            target=assertion_target,
            expected=expected,
            assertion=assertion,
        )
    )
    if len(steps) < 2:
        return None
    kind = {
        "POSITIVE": "works as expected",
        "NEGATIVE": "is prevented as required",
    }.get(category, "handles the stated boundary")
    variant = f" ({dropdown_variant + 1})" if dropdown_variant else ""
    action_names = [
        str(step.target.element_name or "")
        for step in steps
        if step.action in {"click", "fill", "select"} and step.target.element_name
    ]
    subject = action_names[0] if action_names else (ac.get("id") or "scenario")
    return TestCaseSpec(
        title=f"{subject}: {kind}{variant}",
        objective=f"Verify observed UI for {ac.get('id')} ({category.lower()}).",
        category=category,  # type: ignore[arg-type]
        preconditions=[f"Browser is on {_url_path(start)}"],
        steps=steps,
        expected_result=expected,
        test_data=test_data,
        traceability=[str(ac["id"])],
        confidence=confidence if valid else 0.6,
    )


def _coverage_ac(acceptance_criteria: list[dict[str, Any]], state: dict[str, Any], element: dict[str, Any]) -> str:
    """Attach map coverage to the most relevant approved criterion.

    Test cases retain requirement traceability, while their scenario and target
    come from the browser-observed application map.
    """
    context = " ".join((str(state.get("url_pattern") or ""), str(element.get("name") or "")))
    terms = set(re.findall(r"[a-zA-Z0-9]{3,}", context.lower()))
    ranked = []
    for ac in acceptance_criteria:
        ac_terms = set(re.findall(r"[a-zA-Z0-9]{3,}", str(ac.get("text") or "").lower()))
        ranked.append((len(terms & ac_terms), str(ac["id"])))
    return max(ranked, key=lambda item: item[0])[1]


def _page_label(state: dict[str, Any]) -> str:
    path = _url_path(state).strip("/")
    return re.sub(r"[-_]+", " ", path).title() or "Application"


def generate_discovery_coverage_cases(
    acceptance_criteria: list[dict[str, Any]], states: list[dict[str, Any]]
) -> list[TestCaseSpec]:
    """Create bounded tester-style coverage directly from observed controls.

    This complements AC-led scenarios. It never invents a click result: a
    navigation case is emitted only when discovery recorded its destination.
    Other interactive components receive a page-level availability assertion;
    required inputs additionally receive blank and one-character checks.
    """
    if not acceptance_criteria:
        return []
    cases: list[TestCaseSpec] = []
    for state in states:
        for element in _observed(state):
            if not _usable(element, _COVERABLE):
                continue
            name = str(element.get("name") or element.get("text") or "control").strip()
            if not name:
                continue
            ac_id = _coverage_ac(acceptance_criteria, state, element)
            role = str(element.get("role") or "control")
            target = _target(state, element)
            result, confidence = _result_state(states, state, element if role in _CLICKABLE else None)
            if role in _CLICKABLE and result["state_code"] != state["state_code"]:
                assert_target, assertion, expected = _content_assertion(result, name)
                cases.append(TestCaseSpec(
                    title=f"{_page_label(state)}: navigate using {name}",
                    objective="Verify the observed control reaches its discovered destination.",
                    category="POSITIVE", preconditions=[f"Browser is on {_url_path(state)}"],
                    steps=[
                        TestStep(step_number=1, action="navigate", target=_target(state)),
                        TestStep(step_number=2, action="click", target=target),
                        TestStep(step_number=3, action="assert", target=assert_target,
                                 expected=expected, assertion=assertion),
                    ], expected_result=expected, test_data={}, traceability=[ac_id], confidence=confidence,
                ))
                continue
            validation = element.get("validation") or {}
            if role in _FILLABLE and validation.get("required"):
                code = str(element.get("element_code") or "")
                for category, value, suffix, expected, kind in (
                    ("NEGATIVE", "", "empty required input", "Required-field validation reports this field as missing.", "required"),
                    ("EDGE_CASE", "a", "one-character boundary", "Required-field validation no longer reports this field as missing; other validation rules may still reject the value.", "visible"),
                ):
                    cases.append(TestCaseSpec(
                        title=f"{_page_label(state)}: {name} {suffix}",
                        objective="Verify the observed required-field constraint.", category=category,
                        preconditions=[f"Browser is on {_url_path(state)}"],
                        steps=[TestStep(step_number=1, action="navigate", target=_target(state)),
                               TestStep(step_number=2, action="fill", target=target, value="{field_value}"),
                               TestStep(step_number=3, action="assert", target=target, expected=expected,
                                        assertion={"kind": kind, "target": code})],
                        expected_result=expected, test_data={"field_value": value}, traceability=[ac_id], confidence=0.8,
                    ))
                continue
            expected = f"{name} is visible and available"
            cases.append(TestCaseSpec(
                title=f"{_page_label(state)}: {name} is available",
                objective="Verify the observed interactive component is available to the user.",
                category="POSITIVE", preconditions=[f"Browser is on {_url_path(state)}"],
                steps=[TestStep(step_number=1, action="navigate", target=_target(state)),
                       TestStep(step_number=2, action="assert", target=target, expected=expected,
                                assertion={"kind": "enabled", "target": str(element.get("element_code") or "")})],
                expected_result=expected, test_data={}, traceability=[ac_id], confidence=0.8,
            ))
    return cases


def generate_cases_from_map(
    acceptance_criteria: list[dict[str, Any]],
    states: list[dict[str, Any]],
    requirement_title: str,
    requested_categories: dict[str, set[str]] | None = None,
    include_discovery_coverage: bool = False,
) -> list[TestCaseSpec]:
    """Return only evidence-supported drafts; never manufacture category pairs."""
    if not states:
        return []
    cases: list[TestCaseSpec] = []
    for ac in acceptance_criteria:
        ranked_states = _rank_states(states, ac, requirement_title)
        wanted = requested_categories.get(ac["id"]) if requested_categories else None
        # Required-field checks do not need a server submission or a fabricated
        # error page. Assert only the observed required constraint, not complete
        # validity (a non-empty value may still violate format or credentials).
        criterion = str(ac.get("text") or "")
        if re.search(r"\b(required|mandatory|missing|empty)\b", criterion, re.I):
            seen_fields: set[tuple[str, str]] = set()
            for state in ranked_states:
                generated_for_state = False
                for element in _observed(state):
                    name = str(element.get("name") or "")
                    identity = (state["state_code"], str(element.get("element_code")))
                    terms = [word for word in re.findall(r"[a-z]+", name.lower())
                             if word not in {"enter", "your", "the", "answer"}]
                    if (identity in seen_fields or element.get("role") not in {"textbox", "searchbox"}
                        or not (element.get("validation") or {}).get("required")
                        or not any(re.search(r"\b" + re.escape(word) + r"\b", criterion, re.I) for word in terms)):
                        continue
                    seen_fields.add(identity)
                    for category, value, expected in (
                        ("NEGATIVE", "", "Required-field validation reports this field as missing."),
                        ("EDGE_CASE", "a", "Required-field validation no longer reports this field as missing; other validation rules may still reject the value."),
                    ):
                        if wanted and category not in wanted:
                            continue
                        cases.append(TestCaseSpec(
                            title=f"{name}: {'empty required input' if category == 'NEGATIVE' else 'one-character required boundary'}",
                            objective="Verify only the observed required-field constraint.",
                            category=category,
                            preconditions=[f"Browser is on {_url_path(state)}"],
                            steps=[
                                TestStep(step_number=1, action="navigate", target=_target(state)),
                                TestStep(step_number=2, action="fill", target=_target(state, element), value="{field_value}"),
                                TestStep(step_number=3, action="assert", target=_target(state, element), expected=expected,
                                         assertion={"kind": "required" if category == "NEGATIVE" else "visible",
                                                    "target": str(element.get("element_code") or "")}),
                            ],
                            expected_result=expected, test_data={"field_value": value},
                            traceability=[str(ac["id"])], confidence=0.8,
                        ))
                        generated_for_state = True
                # A required-field AC belongs to one page. Do not duplicate it
                # on later pages just because those pages reuse an ID/email
                # field with a similar label.
                if generated_for_state:
                    break
        categories = sorted(wanted) if wanted else _default_categories(ac)
        for category in categories:
            if category not in {"POSITIVE", "NEGATIVE", "EDGE_CASE"}:
                continue
            start = ranked_states[0]
            spec = None
            for candidate in ranked_states:
                spec = _build_case(
                    ac=ac,
                    category=category,
                    start=candidate,
                    states=states,
                    title=requirement_title,
                )
                if spec is not None:
                    start = candidate
                    break
            if spec is not None:
                cases.append(spec)
            if category == "POSITIVE":
                alternate = _build_case(
                    ac=ac,
                    category=category,
                    start=start,
                    states=states,
                    title=requirement_title,
                    dropdown_variant=1,
                )
                if alternate is not None:
                    cases.append(alternate)
    # Whole-graph generation must not be limited to the wording of the ACs.
    # Add observed component coverage unless the caller explicitly requested a
    # small category-repair batch.
    if include_discovery_coverage and not requested_categories:
        cases.extend(generate_discovery_coverage_cases(acceptance_criteria, states))
    return cases
