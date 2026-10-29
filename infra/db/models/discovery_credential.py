"""Encrypted login credentials used only by authenticated discovery."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from infra.db.models import Base


class DiscoveryCredential(Base):
    __tablename__ = "discovery_credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id"),
        nullable=False,
    )

    credential_ref: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True
    )

    label: Mapped[str] = mapped_column(
        String(120), nullable=False, default="Test account"
    )

    role: Mapped[str] = mapped_column(
        String(120), nullable=False, default="User"
    )

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    login_group: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )

    login_route: Mapped[str | None] = mapped_column(
        String(2000), nullable=True
    )

    encrypted_secret: Mapped[str] = mapped_column(
        Text, nullable=False
    )

    test_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="positive"
    )

    credential_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="login"
    )

    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # A null value means the test account has no scheduled expiry.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
