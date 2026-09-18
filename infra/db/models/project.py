"""
Project ORM model — the root entity everything else scopes under (project_id),
used by apps/api/middleware/tenant_scope.py for multi-tenant isolation
(architecture doc Section 28). Multi-tenancy enforcement itself is Phase 3
scope; this model just carries project_id as the FK every other table uses.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db.models import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    application_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
