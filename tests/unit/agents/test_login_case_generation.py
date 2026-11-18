from copy import deepcopy

from core.agents.test_design.scenarios import plan_scenarios
from core.agents.test_design.validation import validate_case


def login_state():
    return {"state_code": "S1", "url_pattern": "/login", "reached_via": [], "elements": [
        {"element_code": "L1", "role": "StaticText", "name": "USERNAME", "control_group": "/form/div[1]", "xpath": "/form/div[1]/label"},
        {"element_code": "U", "role": "textbox", "name": "", "required": True, "control_group": "/form/div[1]", "xpath": "/form/div[1]/input"},
        {"element_code": "L2", "role": "StaticText", "name": "PASSWORD", "control_group": "/form/div[2]", "xpath": "/form/div[2]/label"},
        {"element_code": "P", "role": "textbox", "name": "", "required": True, "input_type": "password", "control_group": "/form/div[2]"},
        {"element_code": "C", "role": "button", "name": "Continue", "input_type": "submit"},
        {"element_code": "B", "role": "link", "name": "Back to home", "url": "http://example.test/"},
    ]}


def generate(texts):
    criteria = [{"id": f"AC-{index}", "text": text} for index, text in enumerate(texts, 1)]
    states = [login_state()]
    original = deepcopy(states)
    plan = plan_scenarios(criteria, states, "Login page")
    cases = plan.cases
    assert states == original
    for case in cases:
        assert not validate_case(case, states, {ac["id"] for ac in criteria}, strict=True,
                                 assertion_sources=texts), case.title
    return cases


def test_login_ui_uses_inputs_instead_of_labels():
    cases = generate(["The username field is displayed", "The password field is displayed", "The Continue button is displayed"])
    assert len(cases) == 1
    assert {step.target.element_code for step in cases[0].steps if step.action == "assert"} == {"U", "P", "C"}


def test_continue_login_retains_compound_outcome_and_observed_identity():
    text = "Clicking Continue with valid credentials authenticates the user and redirects them to the appropriate page"
    cases = generate([text])
    assert len(cases) == 1
    assert cases[0].title == "Verify successful login with valid credentials"
    assert cases[0].expected_result == text
    assert [step.target.element_code for step in cases[0].steps if step.action == "fill"] == ["U", "P"]
    assert all(step.target.element_name == "" for step in cases[0].steps if step.action == "fill")
    assert cases[0].steps[-1].assertion["kind"] == "manual"


def test_password_limits_come_from_requirement_and_do_not_claim_login_success():
    cases = generate(['The system shall accept passwords between 8 and 64 characters and display "Password must be between 8 and 64 characters" for invalid lengths.'])
    assert {len(case.test_data["password"]) for case in cases} == {7, 8, 64, 65}
    assert all(case.steps[-1].assertion["kind"] == "manual" for case in cases)
    assert all("this alone does not establish valid credentials" in case.expected_result
               for case in cases if len(case.test_data["password"]) in {8, 64})


def test_required_messages_and_independent_blank_fields():
    cases = generate(['The system displays "Username is required" when username is blank.',
                      'The system displays "Password is required" when password is blank.'])
    assert len(cases) == 2
    assert any(case.test_data["username"] == "" and case.test_data["password"] != "" for case in cases)
    assert any(case.test_data["password"] == "" and case.test_data["username"] != "" for case in cases)
    assert all('is required"' in case.expected_result for case in cases)
    assert not any("one-character" in case.title or "EL-" in case.title for case in cases)


def test_displaying_back_home_does_not_turn_into_navigation():
    cases = generate(["The Back to Home button is displayed"])
    assert len(cases) == 1
    assert not any(step.action == "click" for step in cases[0].steps)
    assert cases[0].steps[-1].target.element_code == "B"


def test_lockout_message_keeps_quoted_periods_and_remains_manual():
    text = 'Lock the account for 15 minutes after 5 consecutive failed login attempts and display "Account locked. Please try again after 15 minutes."'
    cases = generate([text])
    assert len(cases) == 1
    assert cases[0].expected_result == text
    assert cases[0].steps[-1].assertion["kind"] == "manual"
    assert any("exactly 5" in (step.expected or "") for step in cases[0].steps)


def test_ambiguous_username_labels_are_not_assigned_to_an_input():
    from core.agents.test_design.control_labels import control_label
    state = login_state()
    state["elements"].append({"element_code": "U2", "role": "textbox", "name": "", "control_group": "/form/div[1]"})
    assert control_label(state["elements"][1], state["elements"]) == "U"


def test_clarified_login_error_does_not_repeat_obsolete_ambiguity():
    cases = generate(['The exact error message shown for invalid credentials is not specified.\nThe system shall display "Invalid username or password" for incorrect credentials.'])
    assert len(cases) == 3
    assert all("not specified" not in case.expected_result for case in cases)


def test_unsupported_requested_category_remains_an_explicit_gap():
    plan = plan_scenarios([{"id": "AC-1", "text": "The username field is displayed"}],
                          [login_state()], "Login page", requested_categories={"AC-1": {"NEGATIVE"}})
    assert not plan.cases
    assert len(plan.scenarios) == 1
    assert plan.scenarios[0].category == "NEGATIVE"
    assert plan.scenarios[0].spec is None
