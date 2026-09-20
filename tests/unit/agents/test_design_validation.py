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
from core.agents.test_design.validation import validate_case


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
    assert any("click alone" in issue for issue in issues)


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
