from uuid import uuid4

import pytest

from core.agents.test_execution.eligibility import (
    ExecutionEligibilityError,
    ScriptSnapshot,
    classify_scripts,
)
from domain.enums import AutomationReviewStatus, RiskLevel


def _script(**overrides: object) -> ScriptSnapshot:
    base: dict[str, object] = dict(
        id=uuid4(),
        file_path="tests/login.spec.ts",
        risk_level=RiskLevel.SAFE,
        review_status=AutomationReviewStatus.REVIEWED,
        expected_result="",
        test_case_code="TC-001",
    )
    base.update(overrides)
    return ScriptSnapshot(**base)  # type: ignore[arg-type]


def test_reviewed_safe_scripts_are_runnable() -> None:
    script = _script()
    classified = classify_scripts([script], None, False)
    assert classified.runnable == [script]
    assert classified.skipped == []


def test_destructive_without_flag_is_skipped_and_raises_if_only_script() -> None:
    script = _script(risk_level=RiskLevel.DESTRUCTIVE, review_status=AutomationReviewStatus.APPROVED)
    with pytest.raises(ExecutionEligibilityError):
        classify_scripts([script], None, False)


def test_approved_destructive_runs_when_flag_set() -> None:
    script = _script(risk_level=RiskLevel.DESTRUCTIVE, review_status=AutomationReviewStatus.APPROVED)
    classified = classify_scripts([script], None, True)
    assert classified.runnable == [script]


def test_pending_approval_is_skipped_when_other_script_runs() -> None:
    ready = _script()
    pending = _script(review_status=AutomationReviewStatus.PENDING_APPROVAL)
    classified = classify_scripts([ready, pending], None, False)
    assert classified.runnable == [ready]
    assert classified.skipped[0][0] == pending


def test_unknown_script_id_is_rejected() -> None:
    script = _script()
    with pytest.raises(ExecutionEligibilityError, match="is not part of this generation"):
        classify_scripts([script], [uuid4()], False)


def test_unapproved_destructive_stays_skipped_even_with_flag() -> None:
    script = _script(risk_level=RiskLevel.DESTRUCTIVE, review_status=AutomationReviewStatus.REVIEWED)
    with pytest.raises(ExecutionEligibilityError):
        classify_scripts([script], None, True)
