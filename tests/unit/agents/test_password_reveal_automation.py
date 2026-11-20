from uuid import uuid4

from core.agents.automation_generation import templates
from core.agents.automation_generation.execution_plan import prepare_execution
from core.agents.automation_generation.suite import CaseInput, StateInput, _plan_case, _pages_for


def setup_case():
    state = StateInput(state_code="LOGIN", url_pattern="/", elements=[
        {"element_code": "PWD", "role": "textbox", "name": "Password", "input_type": "password", "css_selector": "#login-password", "risk": "SAFE", "source": "OBSERVED_DOM"},
        {"element_code": "SHOW", "role": "button", "name": "Show password", "risk": "SAFE", "source": "OBSERVED_DOM"},
        {"element_code": "SIGN", "role": "button", "name": "Sign in", "risk": "SAFE", "source": "OBSERVED_DOM"},
    ])
    case = CaseInput(test_case_id=uuid4(), tc_code="TC-098", version=1, title="Verify Show password reveals the entered value",
        requirement_id=uuid4(), requirement_version=1, application_map_id=uuid4(), application_map_version=1,
        project_id=uuid4(), category="POSITIVE", objective="Verify password reveal", credential_ref=None,
        preconditions=["Manual verification required for the observed reveal control."],
        expected_result="Password is revealed without changing its entered value.", test_data={"sample_text": "Visibility sample"}, steps=[
            {"action": "navigate", "target": {"state_code": "LOGIN"}},
            {"action": "fill", "target": {"state_code": "LOGIN", "element_code": "PWD"}, "value": "{sample_text}"},
            {"action": "click", "target": {"state_code": "LOGIN", "element_code": "SHOW"}},
            {"action": "assert", "target": {"state_code": "LOGIN", "element_code": "PWD"}, "assertion": {"kind": "manual", "basis": "observed_toggle"}},
        ])
    return case, {"LOGIN": state}


def test_password_reveal_generates_real_assertion_and_no_login_submit():
    case, states = setup_case()
    draft = _plan_case(case, states, set(), uuid4())
    assert not draft.blocked
    assert "clickSignIn" not in draft.source
    assert "expectElementPwdRevealed" in draft.source
    assert any(selector.expression == 'locator("#login-password")' for selector in draft.selectors)
    pages = _pages_for([draft], states)
    actions = pages["LOGIN"]["actions"]
    reveal = next(item for item in actions if item["name"].endswith("Revealed"))
    assert 'toHaveAttribute("type", "text")' in reveal["body"]
    assert "inputValue()) === entered" in reveal["body"]


def test_unsupported_manual_outcome_is_blocked():
    case, states = setup_case()
    case.steps[-1]["assertion"]["basis"] = "invalid"
    result = prepare_execution(case, states)
    assert "Manual verification" in result.blocked_reason


def test_auth_context_explicitly_honors_certificate_setting():
    auth = templates.render_auth(base_url='"https://example.test"')
    assert 'ignoreHTTPSErrors: process.env.PLAYWRIGHT_IGNORE_HTTPS_ERRORS === "true"' in auth
