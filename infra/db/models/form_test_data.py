import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db.models import Base


class FormTestData(Base):
    """Project-owned form fixture explicitly assignable to one test case."""

    __tablename__ = "form_test_data"
    __table_args__ = (
        Index(
            "uq_form_test_data_case_active",
            "project_id",
            "test_case_id",
            unique=True,
            postgresql_where=text("active AND test_case_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True
    )
    test_case_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("test_cases.id"), nullable=True, index=True
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    form_group: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    test_type: Mapped[str] = mapped_column(String(50), nullable=False)
    test_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
