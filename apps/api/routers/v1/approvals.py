"""
Human-approval-gate endpoints — the structural implementation of Section 21.
Every gate in the pipeline posts here through one consistent, auditable path
(the `approvals` table). `target_type` is generic on purpose: requirements and
test cases have side effects wired up; automation scripts and defect filings
will add their own branch in `_apply_target_side_effect`.

An approval is pinned to the target version the reviewer was shown
(`target_version`). If the target has moved on, deciding it is refused.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from domain.enums import ApprovalStatus, RequirementStatus, TestCaseStatus
from infra.db.models.approval import Approval
from infra.db.models.requirement import Requirement, RequirementVersion
from infra.db.models.test_case import TestCase

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalDecisionRequest(BaseModel):
    decided_by: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    target_version: int | None = None
    status: str
    decided_by: str | None
    decided_at: datetime | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[ApprovalResponse])
async def list_pending_approvals(
    project_id: uuid.UUID,
    target_type: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[Approval]:
    query = select(Approval).where(
        Approval.project_id == project_id, Approval.status == ApprovalStatus.PENDING
    )
    if target_type:
        query = query.where(Approval.target_type == target_type)
    result = await session.execute(query.order_by(Approval.requested_at))
    return list(result.scalars().all())


def _ensure_version_current(approval: Approval, current_version: int) -> None:
    if approval.target_version is not None and approval.target_version != current_version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This approval was requested for version {approval.target_version}, "
                f"but the current version is {current_version}. Review the current version."
            ),
        )


async def _requirement_side_effect(
    session: AsyncSession, approval: Approval, new_status: ApprovalStatus
) -> None:
    requirement = await session.get(Requirement, approval.target_id, with_for_update=True)
    if requirement is None or requirement.project_id != approval.project_id:
        raise HTTPException(status_code=409, detail="Approval target is invalid")
    _ensure_version_current(approval, requirement.current_version)
    if new_status == ApprovalStatus.REJECTED:
        requirement.status = RequirementStatus.REJECTED
        return
    version = await session.execute(
        select(RequirementVersion).where(
            RequirementVersion.requirement_id == requirement.id,
            RequirementVersion.version == requirement.current_version,
        )
    )
    current = version.scalar_one_or_none()
    if current is None or any(
        item.get("requires_clarification", True) for item in current.ambiguities
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Current requirement has unresolved ambiguities; get the requirement "
                f"and POST /api/v1/requirements/{requirement.id}/clarifications"
            ),
        )
    requirement.status = RequirementStatus.APPROVED


async def _test_case_side_effect(
    session: AsyncSession, approval: Approval, new_status: ApprovalStatus
) -> None:
    test_case = await session.get(TestCase, approval.target_id, with_for_update=True)
    if test_case is None or test_case.project_id != approval.project_id:
        raise HTTPException(status_code=409, detail="Approval target is invalid")
    _ensure_version_current(approval, test_case.current_version)
    if test_case.status == TestCaseStatus.OUTDATED:
        raise HTTPException(
            status_code=409,
            detail="The requirement changed after this test case was written; regenerate or edit it.",
        )
    test_case.status = (
        TestCaseStatus.APPROVED if new_status == ApprovalStatus.APPROVED else TestCaseStatus.REJECTED
    )


async def _apply_target_side_effect(
    session: AsyncSession, approval: Approval, new_status: ApprovalStatus
) -> None:
    if approval.target_type == "requirement":
        await _requirement_side_effect(session, approval, new_status)
    elif approval.target_type == "test_case":
        await _test_case_side_effect(session, approval, new_status)
    else:
        raise HTTPException(status_code=409, detail="Approval target type is not implemented")


async def _decide(
    session: AsyncSession,
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest,
    new_status: ApprovalStatus,
) -> Approval:
    decided_by = body.decided_by.strip()
    reason = body.reason.strip() if body.reason else None
    if not decided_by:
        raise HTTPException(status_code=422, detail="decided_by must not be blank")
    if new_status == ApprovalStatus.REJECTED and not reason:
        raise HTTPException(status_code=400, detail="reason is required when rejecting")

    # Row lock: two reviewers deciding at once must not both succeed.
    approval = await session.get(Approval, approval_id, with_for_update=True)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Approval already {approval.status}")

    await _apply_target_side_effect(session, approval, new_status)

    approval.status = new_status
    approval.decided_by = decided_by
    approval.reason = reason
    approval.decided_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(approval)
    return approval


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Approval:
    return await _decide(session, approval_id, body, ApprovalStatus.APPROVED)


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Approval:
    return await _decide(session, approval_id, body, ApprovalStatus.REJECTED)
