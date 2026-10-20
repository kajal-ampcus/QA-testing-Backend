"""Generated drafts must be executable against observed map evidence."""

from core.agents.test_design.schemas import (
    StepTarget,
)
from core.agents.test_design.schemas import (
    TestCaseSpec as CaseSpec,
)
from core.agents.test_design.schemas import (
    TestStep as CaseStep,
)
from core.agents.test_design.validation import _ac_verbs, validate_case


def case(*steps, test_data=None):
    return CaseSpec(
        title="Observed scenario",
        objective="Verify observed behavior",
        category="POSITIVE",
        preconditions=["On the observed page"],
        steps=list(steps),
        expected_result="The observed result appears",
        test_data=test_data or {},
        traceability=["AC-1"],
        confidence=0.9,
    )


STATES = [
    {
        "state_code": "STATE-001",
        "url_pattern": "/contact/",
        "elements": [
            {"element_code": "EL-001", "role": "button", "name": "Submit"},
            {"element_code": "EL-002", "role": "StaticText", "name": "Thank you, Site Owner"},
            {"element_code": "EL-003", "role": "textbox", "name": "Email", "required": True},
        ],
    }
]


def target(code=None):
    return StepTarget(state_code="STATE-001", element_code=code)


def test_rejects_click_only_placeholder():
    issues = validate_case(
        case(CaseStep(step_number=1, action="click", target=target("EL-001"))),
        STATES,
        {"AC-1"},
    )
    assert any("click or fill alone" in issue for issue in issues)


def test_rejects_unresolvable_data_and_preexisting_success_text():
    candidate = case(
        CaseStep(step_number=1, action="fill", target=target("EL-003"), value="{user.email}"),
        CaseStep(step_number=2, action="click", target=target("EL-001")),
        CaseStep(step_number=3, action="assert", target=target("EL-002"), expected="Thank you"),
        test_data={"user": "email: person@example.test"},
    )
    issues = validate_case(candidate, STATES, {"AC-1"})
    assert any("cannot be resolved" in issue for issue in issues)
    assert any("pre-existing page text" in issue for issue in issues)


def test_unchanged_url_alone_does_not_prove_a_negative_result():
    states = [
        {
            "state_code": "STATE-001",
            "url_pattern": "/login",
            "elements": [
                {
                    "element_code": "EL-001",
                    "role": "link",
                    "name": "Login",
                    "url": "https://app.test/admin",
                }
            ],
        },
        {"state_code": "STATE-002", "url_pattern": "/dashboard", "elements": []},
    ]
    rejected = CaseSpec(
        title="Empty username is rejected",
        objective="Verify login stays on the page when username is empty",
        category="NEGATIVE",
        preconditions=["On the login page"],
        steps=[
            CaseStep(step_number=1, action="click", target=target("EL-001")),
            CaseStep(
                step_number=2,
                action="assert",
                target=StepTarget(state_code="STATE-001"),
                expected="The current URL includes /login",
            ),
        ],
        expected_result="The current URL includes /login",
        test_data={},
        traceability=["AC-1"],
        confidence=0.6,
    )
    issues = validate_case(rejected, states, {"AC-1"})
    assert any("unchanged page URL" in issue for issue in issues)

    navigated = CaseSpec(
        title="Login opens admin",
        objective="Verify the login control opens admin",
        category="POSITIVE",
        preconditions=["On the login page"],
        steps=[
            CaseStep(step_number=1, action="click", target=target("EL-001")),
            CaseStep(
                step_number=2,
                action="assert",
                target=StepTarget(state_code="STATE-002"),
                expected="The admin page is visible",
            ),
        ],
        expected_result="The admin page is visible",
        test_data={},
        traceability=["AC-1"],
        confidence=0.8,
    )
    issues = validate_case(navigated, states, {"AC-1"})
    assert any("link destination and asserted state disagree" in issue for issue in issues)


def test_accepts_observed_precise_assertion():
    candidate = case(
        CaseStep(step_number=1, action="fill", target=target("EL-003"), value="{email}"),
        CaseStep(
            step_number=2,
            action="assert",
            target=target("EL-003"),
            expected="Email field contains the value",
        ),
        test_data={"email": "person@example.test"},
    )
    assert validate_case(candidate, STATES, {"AC-1"}) == []


def test_rejects_cases_that_do_not_perform_the_acceptance_criterion_verb():
    candidate = case(
        CaseStep(step_number=1, action="click", target=target("EL-001")),
        CaseStep(step_number=2, action="assert", target=target("EL-002"), expected="Thank you"),
    )
    issues = validate_case(
        candidate,
        STATES,
        {"AC-1"},
        {"AC-1": "The user can log out of the application."},
    )
    assert "Case does not perform the 'logout' action the criterion requires." in issues


def test_login_surface_nouns_do_not_require_a_login_action_for_required_field_case():
    candidate = case(
        CaseStep(step_number=1, action="fill", target=target("EL-003"), value=""),
        CaseStep(step_number=2, action="assert", target=target("EL-003"), expected="Email is required"),
    )
    assert validate_case(
        candidate, STATES, {"AC-1"}, {"AC-1": "The login form requires an email address."}
    ) == []


def test_login_page_availability_check_does_not_require_authentication_action():
    candidate = case(
        CaseStep(step_number=1, action="assert", target=target("EL-003"), expected="Email field is visible"),
    )
    assert validate_case(candidate, STATES, {"AC-1"}, {"AC-1": "The login page is available."}) == []


def test_genuine_login_action_remains_required_after_a_login_screen_noun():
    ac = "The login screen is available and the user can log in."
    assert _ac_verbs(ac) == ["login"]
    login_states = [{
        "state_code": "STATE-001", "url_pattern": "/login", "elements": [
            {"element_code": "LOGIN", "role": "button", "name": "Login"},
        ],
    }]
    genuine = case(
        CaseStep(step_number=1, action="click", target=target("LOGIN")),
        CaseStep(step_number=2, action="assert", target=target(), expected="User is authenticated"),
    )
    assert validate_case(genuine, login_states, {"AC-1"}, {"AC-1": ac}) == []

    incorrect = case(
        CaseStep(step_number=1, action="click", target=target("EL-001")),
        CaseStep(step_number=2, action="assert", target=target("EL-002"), expected="Thank you"),
    )
    issues = validate_case(
        incorrect, STATES, {"AC-1"}, {"AC-1": ac},
    )
    assert "Case does not perform the 'login' action the criterion requires." in issues


def test_logout_case_clicking_login_is_rejected():
    states = [{
        "state_code": "STATE-001", "url_pattern": "/", "elements": [
            {"element_code": "LOGIN", "role": "button", "name": "LOGIN"},
            {"element_code": "DONE", "role": "StaticText", "name": "Done"},
        ],
    }]
    candidate = case(
        CaseStep(step_number=1, action="click", target=target("LOGIN")),
        CaseStep(step_number=2, action="assert", target=target("DONE"), expected="Done"),
    )
    assert "Case does not perform the 'logout' action the criterion requires." in validate_case(
        candidate, states, {"AC-1"}, {"AC-1": "The user can log out."}
    )


def test_dashboard_redirection_verb_is_recognized():
    assert "redirect" in _ac_verbs("The user is redirected to the Dashboard after login.")


def test_rejects_url_only_assertions():
    candidate = case(
        CaseStep(step_number=1, action="navigate", target=StepTarget(state_code="STATE-001")),
        CaseStep(
            step_number=2,
            action="assert",
            target=StepTarget(state_code="STATE-001"),
            expected="The URL includes /contact/",
        ),
    )
    issues = validate_case(candidate, STATES, {"AC-1"})
    assert any("only assertion checks the URL" in issue for issue in issues)


def test_allows_structured_url_only_assertions():
    candidate = case(
        CaseStep(step_number=1, action="navigate", target=StepTarget(state_code="STATE-001")),
        CaseStep(
            step_number=2,
            action="assert",
            target=StepTarget(state_code="STATE-001"),
            expected="The URL includes /contact/",
            assertion={"kind": "url", "target": "STATE-001"},
        ),
    )
    issues = validate_case(candidate, STATES, {"AC-1"})
    assert not any("only assertion checks the URL" in issue for issue in issues)
