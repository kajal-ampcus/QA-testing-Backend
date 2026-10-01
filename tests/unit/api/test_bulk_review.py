"""Bulk review submits drafts and approves them in one pass."""

import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from apps.api.routers.v1.test_cases import (
    _approve_test_case_approval,
    _submit_draft_for_approval,
)
from domain.enums import ApprovalStatus, RequirementStatus, TestCaseStatus
from infra.db.models.approval import Approval
from infra.db.models.test_case import TestCase


class Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class FakeSession:
    def __init__(self, *objects: Any) -> None:
        self.objects = {type(item): item for item in objects}
        self.added: list[Any] = []
        self.flushed = False

    async def get(self, model: type, _id: uuid.UUID, **_: Any) -> Any:
        return self.objects.get(model)

    async def execute(self, _query: Any) -> Result:
        return Result(None)

    def add(self, item: Any) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        self.flushed = True


class FakeRequirement:
    def __init__(self, status: RequirementStatus, version: int) -> None:
        self.status = status
        self.current_version = version
        self.project_id = uuid.uuid4()


class FakeReqRepo:
    def __init__(self, pair: tuple[Any, Any] | None) -> None:
        self.pair = pair

    async def get_with_current_version(self, _requirement_id: uuid.UUID) -> Any:
        return self.pair


def _draft(status: TestCaseStatus = TestCaseStatus.DRAFT, req_version: int = 1) -> TestCase:
    return TestCase(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        tc_code="TC-001",
        requirement_id=uuid.uuid4(),
        requirement_version=req_version,
        application_map_id=uuid.uuid4(),
        current_version=1,
        status=status,
    )


@pytest.mark.asyncio
async def test_submit_draft_creates_pending_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    test_case = _draft()
    session = FakeSession()
    monkeypatch.setattr(
        "apps.api.routers.v1.test_cases.RequirementRepository",
        lambda _db: FakeReqRepo((FakeRequirement(RequirementStatus.APPROVED, 1), object())),
    )

    approval = await _submit_draft_for_approval(session, test_case)

    assert test_case.status == TestCaseStatus.PENDING_APPROVAL
    assert approval.status == ApprovalStatus.PENDING
    assert approval.target_id == test_case.id
    assert session.flushed is True


@pytest.mark.asyncio
async def test_submit_draft_rejects_unapproved_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_case = _draft()
    session = FakeSession()
    monkeypatch.setattr(
        "apps.api.routers.v1.test_cases.RequirementRepository",
        lambda _db: FakeReqRepo(
            (FakeRequirement(RequirementStatus.PENDING_APPROVAL, 1), object())
        ),
    )
    with pytest.raises(HTTPException) as error:
        await _submit_draft_for_approval(session, test_case)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_approve_helper_marks_test_case_approved() -> None:
    test_case = _draft(TestCaseStatus.PENDING_APPROVAL)
    approval = Approval(
        id=uuid.uuid4(),
        project_id=test_case.project_id,
        target_type="test_case",
        target_id=test_case.id,
        target_version=1,
        status=ApprovalStatus.PENDING,
    )
    session = FakeSession(approval, test_case)
    await _approve_test_case_approval(session, approval, "Kajal")
    assert test_case.status == TestCaseStatus.APPROVED
    assert approval.status == ApprovalStatus.APPROVED
    assert approval.decided_by == "Kajal"
