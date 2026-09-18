"""
THE single shared agent input/output envelope (companion doc Part 1). Every
per-agent schema extends AgentOutputEnvelope's `artifacts`/`decisions` shape
rather than redefining it (docs/PROJECT_STRUCTURE.md point 7).

The critical enforcement: AgentDecision.evidence has min_length=1, so a
decision object with no cited evidence fails Pydantic validation before it
can reach the orchestrator or a human — this is the schema-level
implementation of the hallucination rule in architecture doc Section 30, not
just a convention.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from domain.enums import EvidenceSource


class AgentRunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class AgentInputEnvelope(BaseModel):
    agent_run_id: UUID
    project_id: UUID
    trigger: str  # "orchestrator_transition" | "manual" | "webhook"
    context_refs: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any]
    constraints: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    decision: str
    reason: str
    evidence: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    source: EvidenceSource


class AgentArtifactRef(BaseModel):
    type: str
    id: str
    version: int


class AgentOutputEnvelope(BaseModel):
    agent_run_id: UUID
    status: AgentRunStatus
    artifacts: list[AgentArtifactRef] = Field(default_factory=list)
    decisions: list[AgentDecision] = Field(default_factory=list)
    requires_human_approval: bool = False
    errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Populated by any agent that calls an LLM — None for purely deterministic
    # agents (Test Execution never sets this, by design). Kept on the shared
    # envelope rather than per-agent so cost/usage is queryable generically
    # across agent_runs without knowing each agent's payload shape.
    token_usage: dict[str, int | str] | None = None
