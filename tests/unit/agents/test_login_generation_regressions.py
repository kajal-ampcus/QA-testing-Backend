from core.agents.test_design.scenarios import plan_scenarios
from core.agents.test_design.validation import validate_case


def login_state():
    return {"state_code": "LOGIN", "url_pattern": "/", "reached_via": [], "elements": [
        {"element_code": "EMAIL", "role": "textbox", "name": "Work email", "input_type": "text"},
        {"element_code": "PASSWORD", "role": "textbox", "name": "Password", "input_type": "password"},
        {"element_code": "SIGNIN", "role": "button", "name": "Sign in", "input_type": "submit"},
        {"element_code": "SHOW", "role": "button", "name": "Show password"},
        {"element_code": "FORGOT", "role": "button", "name": "Forgot password?"},
    ]}


def test_login_requirements_produce_distinct_manual_cases_without_native_required():
    state = login_state()
    criteria = [
        {"id": "A", "text": "Users with empty mandatory fields receive respective validation messages when attempting to sign in"},
        {"id": "B", "text": "The Sign In button validates the Work Mail and password; on success it redirects the user to the dashboard, and on failure displays an appropriate error message"},
    ]
    plan = plan_scenarios(criteria, [state], "Login")
    cases = plan.cases
    titles = {case.title for case in cases}
    assert {"Submit with Work email blank", "Submit with Password blank", "Submit with Work email, Password blank", "Sign in with valid credentials", "Submit with invalid Work email", "Submit with invalid Password"} <= titles
    for case in cases:
        if case.title in titles and case.steps[-1].assertion.get("kind") == "manual":
            assert not validate_case(case, [state], {"A", "B"}, strict=True,
                assertion_sources=[item["text"] for item in criteria if item["id"] in case.traceability])


def test_unauthorized_login_cannot_assert_dashboard_visible():
    from core.agents.test_design.schemas import TestCaseSpec
    state = login_state()
    dashboard = {"state_code": "DASH", "url_pattern": "/", "elements": [{"element_code": "HEAD", "role": "heading", "name": "Dashboard"}]}
    case = TestCaseSpec.model_validate({"title": "Unauthorized users are denied access", "objective": "Verify unauthorized users are denied access", "category": "NEGATIVE", "preconditions": ["On login"], "steps": [
        {"step_number": 1, "action": "navigate", "target": {"state_code": "LOGIN"}},
        {"step_number": 2, "action": "assert", "target": {"state_code": "DASH", "element_code": "HEAD", "element_name": "Dashboard", "element_role": "heading"}, "expected": "Dashboard is visible", "assertion": {"kind": "text", "target": "HEAD", "text": "Dashboard"}},
    ], "expected_result": "Dashboard is visible", "test_data": {}, "traceability": ["A"], "confidence": 1.0})
    assert any("unauthorized login" in issue for issue in validate_case(case, [state, dashboard], {"A"}, strict=True))
