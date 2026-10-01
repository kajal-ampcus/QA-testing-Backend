"""
approvals ORM model (architecture doc Section 21/26) — the single consistent
path every human-approval-gate action goes through. `target_type`/`target_id`
is generic (points at a requirement, a test-case set, an automation script,
a defect proposal, etc.) so this one table serves every gate in Section 21,
rather than a separate approvals table per domain object.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from domain.enums import ApprovalStatus
from infra.db.models import Base


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "requirement" | "test_case_set" | ...
    target_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # The exact target version the reviewer is shown; approving is refused if
    # the target has moved on since the approval was requested.
    target_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ApprovalStatus.PENDING)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)  # user identifier, Phase 1 = free text
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # required on REJECTED
