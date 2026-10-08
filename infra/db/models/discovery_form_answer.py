"""Encrypted values for forms discovery cannot fill by itself."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db.models import Base


class DiscoveryFormAnswer(Base):
    __tablename__ = "discovery_form_answers"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "page_key",
            "form_key",
            name="uq_discovery_form_answers_project_page_form",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    page_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    form_key: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_values: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
