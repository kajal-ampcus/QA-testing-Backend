"""
ApplicationMap + ApplicationMapState ORM models (architecture doc Section
9/26).

Deliberately normalized, not one JSONB blob per map version — this is the
concrete N+1-avoidance design: `application_map_states` is its own table so
incremental writes during a crawl (one INSERT per discovered state) don't
require rewriting an ever-growing document, and the unique
(application_map_id, fingerprint) index turns the crawler's "have I already
seen this state" check — run on every discovered element, potentially
hundreds of times per crawl — into a single indexed lookup instead of an
O(n) scan. Reads that need "the map with all its states" always go through
infra/db/repositories/application_map_repo.py's selectinload()-based query,
never a lazy per-state fetch.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infra.db.models import Base


class ApplicationMap(Base):
    __tablename__ = "application_maps"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_application_maps_project_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    credential_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # RUNNING | COMPLETE | PARTIAL | FAILED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RUNNING")
    termination_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Structured failure evidence — populated when status is FAILED or PARTIAL.
    # Contains login_error, screenshot_ref, console_errors, network_errors,
    # failed_actions, and termination_detail. Used by the UI to show a
    # developer-friendly diagnostic panel instead of a blank error state.
    diagnostic_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)
    discovery_checkpoint: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, default=None
    )
    test_generation_coverage: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    states: Mapped[list["ApplicationMapState"]] = relationship(back_populates="application_map")


class ApplicationMapState(Base):
    __tablename__ = "application_map_states"
    __table_args__ = (
        Index(
            "ix_application_map_states_map_fingerprint",
            "application_map_id",
            "fingerprint",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_map_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("application_maps.id"), nullable=False
    )
    state_code: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "STATE-010"
    url_pattern: Mapped[str] = mapped_column(String(1000), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    reached_via: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Each item: {"role": "button", "name": "Delete Employee", "risk": "DESTRUCTIVE", "source": "OBSERVED_DOM"}
    elements: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    application_map: Mapped["ApplicationMap"] = relationship(back_populates="states")
