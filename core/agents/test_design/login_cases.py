"""Login behavior designs grounded in observed controls and approved criteria.

Business outcomes without an observed oracle remain manual, never guessed
automated passes. Observed names and IDs are preserved in every step target.
"""

import re

from core.agents.test_design.control_labels import control_label
from core.agents.test_design.fast_generate import _observed, _target, _url_path
from core.agents.test_design.schemas import TestCaseSpec, TestStep


def login_controls(state):
    elements = _observed(state)
    inputs = [e for e in elements if e.get("role") in {"textbox", "input", "searchbox"}]
    usernames = [e for e in inputs if re.search(r"\b(username|user name|e-?mail|employee id|user id)\b", control_label(e, elements), re.I)]
    passwords = [e for e in inputs if (e.get("input_type") or e.get("type")) == "password"
                 or re.search(r"\bpassword\b", control_label(e, elements), re.I)]
    submits = [e for e in elements if e.get("role") == "button"
               and re.fullmatch(r"continue|log\s*in|sign\s*in|submit", str(e.get("name") or ""), re.I)]
    if len(usernames) == len(passwords) == len(submits) == 1 and usernames[0] != passwords[0]:
        return usernames[0], passwords[0], submits[0]
    return None


def login_cases(ac: dict, state: dict, criteria: list[dict]) -> list[TestCaseSpec] | None:
    controls = login_controls(state)
    if not controls:
        return None
    username, password, submit = controls
    text = str(ac["text"])
    lower = text.lower()
    ac_id = str(ac["id"])
    fields = [username, password]
    named = [field for field in fields if re.search(
        r"\bpasswords?\b" if field == password else r"\busername\b|\buser name\b", text, re.I)]
    output = []

    def strongest(pattern):
        matching = [str(item.get("text") or "") for item in criteria
                    if re.search(pattern, str(item.get("text") or ""), re.I)]
        selected = max(matching, key=lambda value: ('"' in value, len(value))) if matching else text
        # Retained ambiguity prose is not part of the clarified expected result.
        return re.split(r"\n(?=The system shall\b)", selected)[-1]

    def make(title, expected, category="POSITIVE", values=None, click=False,
             assertion=None, target=None, instructions=None, prerequisites=None):
        target = target or submit
        steps = [TestStep(step_number=1, action="navigate", target=_target(state))]
        data = {}
        for field, value in values or []:
            key = "password" if field == password else "username"
            data[key] = value
            steps.append(TestStep(step_number=len(steps) + 1, action="fill", target=_target(state, field), value="{" + key + "}"))
        if instructions:
            steps.append(TestStep(step_number=len(steps) + 1, action="wait", target=_target(state, target), expected=instructions))
        if click:
            steps.append(TestStep(step_number=len(steps) + 1, action="click", target=_target(state, click if isinstance(click, dict) else submit)))
        manual = assertion is None
        steps.append(TestStep(step_number=len(steps) + 1, action="assert", target=_target(state, target),
            expected=expected, assertion=assertion or {"kind": "manual", "target": target["element_code"],
                "text": expected, "requirement_text": text, "basis": "requirement_behavior"}))
        if manual:
            steps[-1].assertion["fields"] = "|".join(control_label(field, state["elements"])
                for field, value in values or [] if str(value).startswith("INVALID_TEST_FIXTURE:"))
        output.append(TestCaseSpec(title=title, objective=expected, category=category,
            preconditions=[f"Browser is on {_url_path(state)} with a fresh signed-out session.",
                *(["Manual verification required: the outcome is specified by the requirement; an automated result oracle is not established."] if manual else []),
                *(["Use configured test fixtures; do not place real account secrets in test-case data."] if any("TEST_FIXTURE:" in str(value) for value in data.values()) else []),
                *(prerequisites or [])], steps=steps, expected_result=expected, test_data=data,
            traceability=[ac_id], confidence=0.7 if manual else 0.9))

    # Presence obligations must target inputs, never their neighboring label.
    if re.search(r"\b(displayed|visible|loads successfully)\b", lower):
        targets = named or ([submit] if "continue" in lower else [])
        if "loads successfully" in lower:
            targets = [username, password, submit]
        if targets:
            for field in targets:
                label = control_label(field, state["elements"]).title()
                make(f"Verify {label} {'field' if field in fields else 'button'} is displayed",
                     f"{label} is visible", assertion={"kind": "visible", "target": field["element_code"]}, target=field)
            return output
    if re.search(r"\b(masked|masking)\b", lower):
        make("Verify password is masked by default", "Password input uses type=password",
             assertion={"kind": "attribute", "target": password["element_code"], "name": "type", "value": "password"}, target=password)
        return output
    if re.search(r"\b(password|passphrase)\b", lower) and re.search(
        r"\b(visibility|visible|show|hide|reveal|toggle|eye)\b", lower
    ):
        # This is a local input-state interaction, not an authentication flow.
        # Only use a control explicitly observed in the application map: an
        # unlabeled icon must be fixed in the application/discovery data rather
        # than guessed as the form submit button.
        toggles = [element for element in _observed(state)
                   if element.get("role") in {"button", "checkbox"}
                   and re.search(r"\b(show|hide|reveal|toggle|visibility|eye)\b.*\b(password|passphrase)\b|\b(password|passphrase)\b.*\b(show|hide|reveal|toggle|visibility|eye)\b",
                                 control_label(element, state["elements"]), re.I)]
        if len(toggles) != 1:
            return None
        toggle = toggles[0]
        sample_key = "visibility_sample"
        sample_value = "Visibility sample"
        expected = "Password is revealed without changing its entered value."
        steps = [
            TestStep(step_number=1, action="navigate", target=_target(state)),
            TestStep(step_number=2, action="fill", target=_target(state, password), value="{" + sample_key + "}"),
            TestStep(step_number=3, action="assert", target=_target(state, password),
                     expected="Password input uses type=password",
                     assertion={"kind": "attribute", "target": password["element_code"], "name": "type", "value": "password"}),
            TestStep(step_number=4, action="click", target=_target(state, toggle)),
            TestStep(step_number=5, action="assert", target=_target(state, password),
                     expected="Password input uses type=text",
                     assertion={"kind": "attribute", "target": password["element_code"], "name": "type", "value": "text"}),
            TestStep(step_number=6, action="assert", target=_target(state, password), expected=expected,
                     assertion={"kind": "value", "target": password["element_code"], "value": sample_value}),
        ]
        return [TestCaseSpec(
            title="Verify password visibility toggle preserves the entered value",
            objective=text,
            category="POSITIVE",
            preconditions=[f"Browser is on {_url_path(state)} with a fresh signed-out session."],
            steps=steps,
            expected_result=expected,
            test_data={sample_key: sample_value},
            traceability=[ac_id],
            confidence=0.9,
        )]
    if "back to home" in lower:
        if re.search(r"does not submit|without submitting", lower):
            links = [e for e in _observed(state) if e.get("role") in {"link", "button"}
                     and re.search(r"back to home", str(e.get("name") or ""), re.I)]
            if len(links) == 1:
                make("Verify Back to Home does not submit credentials", text,
                     values=[(username, "VALID_TEST_FIXTURE:username"), (password, "VALID_TEST_FIXTURE:password")],
                     click=links[0], target=links[0], prerequisites=["Inspect network requests while navigating; verify no authentication submission is sent."])
                return output
        return None  # Observed link/destination handling remains in the general planner.
    if re.search(r"\b(length|characters)\b", lower) and password in named:
        rules = " ".join(str(item.get("text") or "") for item in criteria if re.search(r"\bpasswords?\b", str(item.get("text") or ""), re.I))
        limit = re.search(r"between\s+(\d+)\s+and\s+(\d+)\s+characters", rules, re.I)
        if not limit:
            return None
        minimum, maximum = map(int, limit.groups())
        if not 1 <= minimum <= maximum <= 4096:
            return None
        for size in sorted({max(0, minimum - 1), minimum, maximum, maximum + 1}):
            accepted = minimum <= size <= maximum
            expected = (f"Password length {size} is accepted by the {minimum}–{maximum} character length rule; this alone does not establish valid credentials."
                        if accepted else f"Password length {size} is rejected by the {minimum}–{maximum} character length rule.")
            message = next((m for item in criteria for m in re.findall(r'"([^"\n]+)"', str(item.get("text") or ""))
                            if "password must be between" in m.lower()), None)
            if not accepted and message:
                expected += f' Validation message: "{message}".'
            make(f"Verify password length boundary of {size} characters", expected, "EDGE_CASE",
                 [(username, "VALID_TEST_FIXTURE:username"), (password, "a" * size)], click=True,
                 prerequisites=["Use a dedicated account whose password matches the boundary value when checking authentication; otherwise inspect length validation independently of credential rejection."])
        return output
    if re.search(r"spaces.only|whitespace.only", lower):
        for field in named or fields:
            label = "password" if field == password else "username"
            values = [(e, "   " if e == field else f"VALID_TEST_FIXTURE:{'password' if e == password else 'username'}") for e in fields]
            make(f"Verify {label} rejects spaces-only input", text, "NEGATIVE", values, click=True)
        return output
    if re.search(r"\b(blank|mandatory|required)\b", lower) and not re.search(r"\benabled\b", lower):
        if not named and not re.search(r"\b(fields|inputs|login|mandatory fields)\b", lower):
            return None  # Preserve native constraints for specifically named non-login fields.
        selected = named or fields
        for field in selected:
            label = "password" if field == password else "username"
            values = [(e, "" if e == field else f"VALID_TEST_FIXTURE:{'password' if e == password else 'username'}") for e in fields]
            message = next((m for item in criteria for m in re.findall(r'"([^"\n]+)"', str(item.get("text") or ""))
                            if m.lower() == f"{label} is required"), None)
            expected = f'Login is blocked; "{message}" is displayed.' if message else text
            make(f"Verify login validation when {label} is blank", expected, "NEGATIVE", values, click=True)
        if not named:
            make("Verify login is blocked when both credentials are blank", text, "NEGATIVE",
                 [(username, ""), (password, "")], click=True)
        return output
    if "enabled" in lower and "continue" in lower:
        make("Verify Continue is enabled with nonblank credentials", text,
             values=[(username, "VALID_TEST_FIXTURE:username"), (password, "VALID_TEST_FIXTURE:password")],
             assertion={"kind": "enabled", "target": submit["element_code"]})
        return output
    if re.search(r"accepts? a valid (username|password)", lower):
        field = password if "password" in lower else username
        label = "password" if field == password else "username"
        make(f"Verify {label} accepts valid input", text, values=[(field, f"VALID_TEST_FIXTURE:{label}")], target=field)
        return output
    if re.search(r"lock|repeated|consecutive", lower):
        policy = " ".join(str(item.get("text") or "") for item in criteria)
        attempts = re.search(r"after\s+(\d+)\s+(?:consecutive\s+)?(?:unsuccessful|failed)", policy, re.I)
        if not attempts:
            return None
        count = int(attempts[1])
        make(f"Verify account lockout after {count} failed login attempts", strongest(r"lock.*after\s+\d+"), "NEGATIVE",
             values=[(username, "VALID_TEST_FIXTURE:username"), (password, "INVALID_TEST_FIXTURE:password")],
             instructions=f"Manually submit exactly {count} consecutive failed login attempts. Verify the configured lockout duration and message; do not run against a shared account.",
             prerequisites=["Use a dedicated resettable test account with its failed-attempt counter reset. Approved lockout policy must be available."])
        return output
    if re.search(r"inactive", lower):
        make("Verify inactive account cannot log in", text, "NEGATIVE",
             [(username, "INACTIVE_TEST_FIXTURE:username"), (password, "INACTIVE_TEST_FIXTURE:password")], click=True,
             prerequisites=["A seeded inactive account with known credentials is required."])
        return output
    if re.search(r"protected pages|without authentication", lower):
        make("Verify protected pages require authentication", text, "NEGATIVE", target=username,
             instructions="In a fresh signed-out browser, manually open the configured protected-page test URL and verify access is denied or sign-in is required.",
             prerequisites=["Configure a known protected-page URL. The current application map does not establish its destination, so this check requires manual setup."])
        return output
    if re.search(r"sql injection|parameterized", lower):
        make("Verify login safely handles SQL injection input", strongest(r"SQL injection"), "NEGATIVE",
             [(username, "' OR '1'='1' --"), (password, "INVALID_TEST_FIXTURE:password")], click=True,
             prerequisites=["Use an isolated test environment. Verify server/database evidence separately to establish parameterized queries; browser behavior alone cannot prove their use."])
        return output
    if re.search(r"duplicate|multiple times", lower):
        make("Verify repeated Continue activation does not duplicate login requests", text, "EDGE_CASE",
             [(username, "VALID_TEST_FIXTURE:username"), (password, "VALID_TEST_FIXTURE:password")],
             instructions="Manually activate Continue rapidly more than once and inspect authentication requests and resulting session creation.")
        return output
    if re.search(r"invalid|incorrect|unsupported|format", lower):
        selected = named or fields
        formatting = bool(re.search(r"format|unsupported", lower))
        expected = strongest(r"username.*(?:format|unsupported)") if formatting else strongest(r"invalid.*credentials|incorrect.*credentials")
        for field in selected:
            label = "password" if field == password else "username"
            make(f"Verify {'unsupported username format' if formatting else 'login rejects invalid ' + label}", expected, "NEGATIVE",
                 [(e, f"{'INVALID' if e == field else 'VALID'}_TEST_FIXTURE:{'password' if e == password else 'username'}") for e in fields], click=True,
                 prerequisites=["Use confirmed invalid fixtures under the approved format rules; keep the other credential valid."])
        if len(selected) == 2 and not formatting:
            make("Verify login rejects invalid username and password", expected, "NEGATIVE",
                 [(username, "INVALID_TEST_FIXTURE:username"), (password, "INVALID_TEST_FIXTURE:password")], click=True,
                 prerequisites=["Use a confirmed invalid account pair; verify the same approved credential-rejection outcome."])
        return output
    if re.search(r"valid credentials|authenticates|successful login", lower):
        make("Verify successful login with valid credentials", text,
             values=[(username, "VALID_TEST_FIXTURE:username"), (password, "VALID_TEST_FIXTURE:password")], click=True)
        return output
    return None
