"""
Requirement + RequirementVersion ORM models (architecture doc Section 26).

Append-only versioning (Section 23): `requirements` is the stable REQ-ID +
a pointer to the current version; `requirement_versions` never gets an
existing row overwritten, only new rows appended. This is what lets a test
case reference "REQ-EMP-001 @ version 1" and keep meaning that exact wording
even after a later edit creates version 2.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from domain.enums import RequirementStatus
from infra.db.models import Base


class Requirement(Base):
    """The stable identity: REQ-ID + which version is current + approval status."""

    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("project_id", "req_code", name="uq_requirements_project_code"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    req_code: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "REQ-EMP-001"
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=RequirementStatus.PENDING_APPROVAL)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    versions: Mapped[list["RequirementVersion"]] = relationship(back_populates="requirement")


class RequirementVersion(Base):
    """Append-only. One row per version — never updated after creation."""

    __tablename__ = "requirement_versions"
    __table_args__ = (
        UniqueConstraint("requirement_id", "version", name="uq_requirement_versions_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("requirements.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Each item: {"id": "AC-1", "text": "...", "source": "REQUIREMENT"|"INFERENCE"}
    acceptance_criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    ambiguities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    domain_tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    requirement: Mapped["Requirement"] = relationship(back_populates="versions")
