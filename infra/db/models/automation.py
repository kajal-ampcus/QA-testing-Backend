"""
AutomationScript + automation_versions ORM models (architecture doc Section
26). selector_strategy is JSONB — the observed locator evidence for each step.

A generation is the set of script rows that share generation_id. Versions are
append-only, including the review status recorded when a tester approves or
rejects a non-safe script.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from domain.enums import AutomationReviewStatus
from infra.db.models import Base


class AutomationScript(Base):
    __tablename__ = "automation_scripts"
    __table_args__ = (
        UniqueConstraint("project_id", "script_code", name="uq_automation_scripts_project_code"),
        Index("ix_automation_scripts_project_id", "project_id"),
        Index("ix_automation_scripts_generation_id", "generation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    script_code: Mapped[str] = mapped_column(String(50), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("test_cases.id"), nullable=False
    )
    test_case_code: Mapped[str] = mapped_column(String(50), nullable=False)
    test_case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    requirement_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    application_map_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("application_maps.id"), nullable=False
    )
    application_map_version: Mapped[int] = mapped_column(Integer, nullable=False)
    framework: Mapped[str] = mapped_column(String(30), nullable=False, default="playwright")
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    selector_strategy: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=AutomationReviewStatus.REVIEWED
    )
    review_findings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    suite_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    versions: Mapped[list["AutomationVersion"]] = relationship(back_populates="script")


class AutomationVersion(Base):
    __tablename__ = "automation_versions"
    __table_args__ = (
        UniqueConstraint(
            "automation_script_id", "version", name="uq_automation_versions_number"
        ),
        Index("ix_automation_versions_script_id", "automation_script_id"),
        Index("ix_automation_versions_generation_id", "generation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    automation_script_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("automation_scripts.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    selector_strategy: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    review_status: Mapped[str] = mapped_column(String(30), nullable=False)
    review_findings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    suite_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    script: Mapped["AutomationScript"] = relationship(back_populates="versions")
