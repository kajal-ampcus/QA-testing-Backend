from core.agents.test_case_validation.agent import TestCaseValidationAgent
from core.agents.test_design.schemas import StepTarget
from core.agents.test_design.schemas import TestCaseSpec as CaseSpec
from core.agents.test_design.schemas import TestStep as CaseStep


def _case(state: str = "STATE-001", element: str | None = "EL-001") -> CaseSpec:
    return CaseSpec(
        title="Login succeeds",
        objective="Verify login",
        category="POSITIVE",
        preconditions=["On login"],
        steps=[
            CaseStep(step_number=1, action="navigate", target=StepTarget(state_code=state)),
            CaseStep(
                step_number=2,
                action="assert",
                target=StepTarget(state_code=state, element_code=element),
                expected="The current URL includes /login",
            ),
        ],
        expected_result="The current URL includes /login",
        test_data={},
        traceability=["AC-1"],
        confidence=0.8,
    )


def test_unknown_element_fails_map_check() -> None:
    states = [
        {
            "state_code": "STATE-001",
            "url_pattern": "/login",
            "elements": [{"element_code": "EL-001", "name": "Email", "role": "textbox"}],
        }
    ]
    verdict = TestCaseValidationAgent().validate(_case(element="EL-999"), states, {"AC-1"})
    assert verdict.passed is False
    assert verdict.checks["element_exists_in_map"] is False
    assert any("EL-999" in issue for issue in verdict.issues)
