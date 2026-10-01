"""Approval decisions are pinned to the version the reviewer saw."""

import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from apps.api.routers.v1.approvals import ApprovalDecisionRequest, _decide
from domain import enums
from domain.enums import ApprovalStatus, RequirementStatus
from infra.db.models import test_case as test_case_models
from infra.db.models.approval import Approval
from infra.db.models.requirement import Requirement, RequirementVersion


class Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class FakeSession:
    def __init__(self, *objects: Any, version: RequirementVersion | None = None) -> None:
        self.objects = {type(item): item for item in objects}
        self.version = version
        self.committed = False
        self.added: list[Any] = []

    async def get(self, model: type, _id: uuid.UUID, **_: Any) -> Any:
        return self.objects.get(model)

    async def execute(self, _query: Any) -> Result:
        return Result(self.version)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _item: Any) -> None:
        pass

    def add(self, item: Any) -> None:
        self.added.append(item)


def _approval(target_type: str, target: Any, version: int) -> Approval:
    return Approval(
        id=uuid.uuid4(),
        project_id=target.project_id,
        target_type=target_type,
        target_id=target.id,
        target_version=version,
        status=ApprovalStatus.PENDING,
    )


def _requirement(current_version: int) -> Requirement:
    return Requirement(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        req_code="REQ-001",
        current_version=current_version,
        status=RequirementStatus.PENDING_APPROVAL,
    )


def _version(ambiguities: list[dict[str, Any]]) -> RequirementVersion:
    return RequirementVersion(version=1, ambiguities=ambiguities, acceptance_criteria=[])


APPROVE = ApprovalDecisionRequest(decided_by="tester")
REJECT = ApprovalDecisionRequest(decided_by="tester", reason="Wrong scope")


@pytest.mark.asyncio
async def test_stale_requirement_approval_is_refused() -> None:
    requirement = _requirement(current_version=2)
    approval = _approval("requirement", requirement, version=1)
    session = FakeSession(approval, requirement, version=_version([]))
    with pytest.raises(HTTPException) as error:
        await _decide(session, approval.id, APPROVE, ApprovalStatus.APPROVED)  # type: ignore[arg-type]
    assert error.value.status_code == 409
    assert approval.status == ApprovalStatus.PENDING and not session.committed


@pytest.mark.asyncio
async def test_informational_ambiguity_does_not_block_approval() -> None:
    requirement = _requirement(current_version=1)
    approval = _approval("requirement", requirement, version=1)
    info = [{"field": "copy", "issue": "Tone", "requires_clarification": False}]
    session = FakeSession(approval, requirement, version=_version(info))
    await _decide(session, approval.id, APPROVE, ApprovalStatus.APPROVED)  # type: ignore[arg-type]
    assert requirement.status == RequirementStatus.APPROVED


@pytest.mark.asyncio
async def test_blocking_ambiguity_blocks_approval() -> None:
    requirement = _requirement(current_version=1)
    approval = _approval("requirement", requirement, version=1)
    blocking = [{"field": "timing", "issue": "Quickly", "requires_clarification": True}]
    session = FakeSession(approval, requirement, version=_version(blocking))
    with pytest.raises(HTTPException) as error:
        await _decide(session, approval.id, APPROVE, ApprovalStatus.APPROVED)  # type: ignore[arg-type]
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_rejecting_requirement_marks_it_rejected() -> None:
    requirement = _requirement(current_version=1)
    approval = _approval("requirement", requirement, version=1)
    session = FakeSession(approval, requirement)
    await _decide(session, approval.id, REJECT, ApprovalStatus.REJECTED)  # type: ignore[arg-type]
    assert requirement.status == RequirementStatus.REJECTED
    assert approval.status == ApprovalStatus.REJECTED and approval.reason == "Wrong scope"


@pytest.mark.asyncio
async def test_test_case_approval_sets_case_status() -> None:
    case = test_case_models.TestCase(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        tc_code="TC-001",
        current_version=1,
        status=enums.TestCaseStatus.PENDING_APPROVAL,
    )
    approval = _approval("test_case", case, version=1)
    session = FakeSession(approval, case)
    await _decide(session, approval.id, APPROVE, ApprovalStatus.APPROVED)  # type: ignore[arg-type]
    assert case.status == enums.TestCaseStatus.APPROVED


@pytest.mark.asyncio
async def test_automation_approval_appends_a_reviewed_version() -> None:
    from infra.db.models.automation import AutomationScript

    script = AutomationScript(
        id=uuid.uuid4(),
        script_code="AUTO-001",
        project_id=uuid.uuid4(),
        generation_id=uuid.uuid4(),
        test_case_id=uuid.uuid4(),
        test_case_code="TC-001",
        test_case_version=1,
        application_map_id=uuid.uuid4(),
        application_map_version=1,
        framework="playwright",
        file_path="tests/destructive/TC-001.delete.spec.ts",
        selector_strategy=[],
        risk_level=enums.RiskLevel.DESTRUCTIVE,
        review_status=enums.AutomationReviewStatus.PENDING_APPROVAL,
        review_findings={},
        suite_summary={},
        current_version=1,
    )
    approval = _approval("automation_script", script, version=1)
    session = FakeSession(approval, script)
    await _decide(session, approval.id, APPROVE, ApprovalStatus.APPROVED)  # type: ignore[arg-type]
    assert script.review_status == enums.AutomationReviewStatus.APPROVED
    assert script.current_version == 2
    assert session.added and session.committed
