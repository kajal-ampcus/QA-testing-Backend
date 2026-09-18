"""
audit_logs ORM model (architecture doc Section 26/28) — every MCP/tool call
logged with the requesting agent, tool, and parameters (minus secrets).
Complementary to agent_runs: agent_runs is "what did the agent decide,"
audit_logs is "what did the agent actually touch." Milestone 1 doesn't call
any MCP tools (Requirement Understanding has zero tool access by design —
Section 6 Agent 1), so this table stays empty until Milestone 2's Discovery
Agent starts writing to it.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from infra.db.models import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "chrome_devtools_mcp.click"
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
