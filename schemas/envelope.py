"""
THE single shared agent input/output envelope (companion doc Part 1). Every
per-agent schema (core/agents/*/schemas.py) extends this rather than
redefining agent_run_id/status/decisions[] from scratch
(docs/PROJECT_STRUCTURE.md point 7) — this is what keeps the orchestrator's
routing logic generic instead of needing a branch per agent.

Input envelope: agent_run_id, project_id, trigger, context_refs, payload,
constraints{confidence_threshold, max_retries}.

Output envelope: agent_run_id, status (SUCCESS|PARTIAL|BLOCKED|FAILED),
artifacts[], decisions[] (each: decision, reason, evidence[], confidence,
source), requires_human_approval, errors[].

A decision object missing `evidence`/`source` must fail validation here —
this is the schema-level enforcement of the hallucination rule (architecture
doc Section 30).

Phase 0 stub.
"""

# TODO (Phase 1):
# class AgentInputEnvelope(BaseModel): ...
# class AgentDecision(BaseModel):
#     decision: str
#     reason: str
#     evidence: list[str]      # non-empty — enforce via Field(min_length=1)
#     confidence: float
#     source: EvidenceSource
# class AgentOutputEnvelope(BaseModel): ...
