"""
Human-approval-gate endpoints — the structural implementation of Section 21.
Every gate in the pipeline posts here through one consistent, auditable path
(the `approvals` table). `target_type` is generic on purpose — Milestone 1
only has "requirement" side effects wired up, but validated test-case sets,
automation scripts, and defect filings will reuse this exact endpoint in
later milestones, just adding a new `target_type` branch below.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from domain.enums import ApprovalStatus, RequirementStatus
from infra.db.models.approval import Approval
from infra.db.models.requirement import Requirement, RequirementVersion

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalDecisionRequest(BaseModel):
    decided_by: str
    reason: str | None = None


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    status: str
    decided_by: str | None
    decided_at: datetime | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[ApprovalResponse])
async def list_pending_approvals(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> list[Approval]:
    result = await session.execute(
        select(Approval).where(Approval.project_id == project_id, Approval.status == ApprovalStatus.PENDING)
    )
    return list(result.scalars().all())


async def _apply_target_side_effect(
    session: AsyncSession, approval: Approval, new_status: ApprovalStatus
) -> None:
    """Each target_type gets its own side effect on approve/reject. Milestone 1
    only implements "requirement" — extend this as later milestones add
    approval gates for validated test-case sets, automation, and defects."""
    if approval.target_type != "requirement":
        raise HTTPException(status_code=409, detail="Approval target type is not implemented")
    requirement = await session.get(Requirement, approval.target_id)
    if requirement is None or requirement.project_id != approval.project_id:
        raise HTTPException(status_code=409, detail="Approval target is invalid")
    if new_status == ApprovalStatus.APPROVED:
        version = await session.execute(
            select(RequirementVersion).where(
                RequirementVersion.requirement_id == requirement.id,
                RequirementVersion.version == requirement.current_version,
            )
        )
        current = version.scalar_one_or_none()
        if current is None or current.ambiguities:
            raise HTTPException(
                status_code=409,
                detail="Resolve ambiguities with a requirement revision before approval",
            )
        requirement.status = RequirementStatus.APPROVED


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Approval:
    approval = await session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Approval already {approval.status}")

    approval.status = ApprovalStatus.APPROVED
    approval.decided_by = body.decided_by
    approval.reason = body.reason
    approval.decided_at = datetime.now(UTC)

    await _apply_target_side_effect(session, approval, ApprovalStatus.APPROVED)

    await session.commit()
    await session.refresh(approval)
    return approval


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Approval:
    if not body.reason:
        raise HTTPException(status_code=400, detail="reason is required when rejecting")

    approval = await session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Approval already {approval.status}")

    approval.status = ApprovalStatus.REJECTED
    approval.decided_by = body.decided_by
    approval.reason = body.reason
    approval.decided_at = datetime.now(UTC)

    await _apply_target_side_effect(session, approval, ApprovalStatus.REJECTED)

    await session.commit()
    await session.refresh(approval)
    return approval
