"""
Agent 4 — Test Case Validation Agent.

Independent auditor of Test Design's output. Deterministic map lookups run
first; confidence is derived from those checks, never from a model score.
"""

from typing import Any

from core.agents.base import BaseAgent
from core.agents.test_case_validation.schemas import ValidationVerdict
from core.agents.test_design.schemas import TestCaseSpec
from core.agents.test_design.validation import validate_case
from core.confidence.scoring import compute_confidence
from schemas.envelope import AgentInputEnvelope


class TestCaseValidationAgent(BaseAgent[ValidationVerdict]):
    name = "test_case_validation"

    def issues_for(
        self,
        case: TestCaseSpec,
        states: list[dict[str, Any]],
        ac_ids: set[str],
        ac_texts: dict[str, str] | None = None,
        *, strict: bool = False, assertion_sources: list[str] | None = None,
    ) -> list[str]:
        return self.validate(case, states, ac_ids, ac_texts, strict=strict, assertion_sources=assertion_sources).issues

    def validate(
        self,
        case: TestCaseSpec,
        states: list[dict[str, Any]],
        ac_ids: set[str],
        ac_texts: dict[str, str] | None = None,
        *, strict: bool = False, assertion_sources: list[str] | None = None,
    ) -> ValidationVerdict:
        issues = validate_case(case, states, ac_ids, ac_texts, strict=strict, assertion_sources=assertion_sources)
        by_code = {state["state_code"]: state for state in states}
        element_ok = all(
            (
                step.target.state_code in by_code
                and (
                    not step.target.element_code
                    or any(
                        element.get("element_code") == step.target.element_code
                        for element in by_code[step.target.state_code].get("elements", [])
                    )
                )
            )
            for step in case.steps
        )
        checks = {
            "has_steps": bool(case.steps),
            "last_step_is_assert": bool(case.steps) and case.steps[-1].action == "assert",
            "element_exists_in_map": element_ok,
            "traceability_known": not (set(case.traceability) - ac_ids),
        }
        assertions = [step for step in case.steps if step.action == 'assert']
        manual_count = sum((step.assertion or {}).get('kind') == 'manual' for step in assertions)
        checks['automation_oracle_observed'] = manual_count == 0
        passed = not issues
        confidence = compute_confidence(
            evidence_count=len(case.steps) + len(case.traceability),
            check_pass_rate=(sum(1 for ok in checks.values() if ok) / len(checks)),
            inference_share=manual_count / len(assertions) if manual_count else (0.0 if passed else 0.4),
        )
        return ValidationVerdict(
            passed=passed, issues=issues, checks=checks, confidence=confidence
        )

    async def run(self, request: AgentInputEnvelope) -> ValidationVerdict:
        case = TestCaseSpec.model_validate(request.payload["test_case"])
        states = list(request.payload.get("states") or [])
        ac_ids = set(request.payload.get("ac_ids") or [])
        ac_texts = dict(request.payload.get("ac_texts") or {})
        return self.validate(case, states, ac_ids, ac_texts)
