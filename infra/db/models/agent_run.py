"""
agent_runs ORM model (architecture doc Section 26) — every agent invocation,
its full input/output envelope, linked to whichever artifact it produced.
Backs the "Agent Activity" transparency screen (Section 27) and is the audit
trail for Appendix A Q30 ("how is every AI decision audited"). Stores the
envelope as JSONB rather than a normalized table per decision — Section 24's
"not vector-DB-everything" principle applies here too: this is structured
data, but its shape varies per agent, so JSONB is the right fit, not a rigid
per-field schema that would need a migration every time an agent's output
shape changes slightly.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db.models import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)  # "requirement_understanding", etc.
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # AgentRunStatus value
    input_envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
