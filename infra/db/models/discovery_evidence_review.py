"""Project-scoped review flags for discovery screenshots.

A review follows the screen fingerprint, so a later map version can collapse
a screen that was already accepted without copying the PNG.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db.models import Base


class DiscoveryEvidenceReview(Base):
    __tablename__ = "discovery_evidence_reviews"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "fingerprint",
            name="uq_discovery_evidence_reviews_project_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="reviewed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
