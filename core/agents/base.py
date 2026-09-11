"""
BaseAgent — enforces the shared input/output envelope every agent uses
(companion doc Part 1):

Input:  agent_run_id, project_id, trigger, context_refs, payload, constraints
Output: agent_run_id, status, artifacts[], decisions[] (each with
        evidence[] + source), requires_human_approval, errors[]

Why this exists as a base class rather than each agent defining its own
shape: the Orchestrator routes on structure, not prose (companion doc Part 1,
point 1) — every agent's output must be parseable the same generic way. A
decision object missing `evidence`/`source` should fail validation here,
before it ever reaches the orchestrator or a human — that is the schema-level
enforcement of the hallucination rule in architecture doc Section 30.

Concrete per-agent payload/artifact shapes live in each agent's schemas.py
and extend the envelope defined in schemas/envelope.py (the single shared
pydantic definition — see docs/PROJECT_STRUCTURE.md point 7).

Phase 0 stub.
"""

# TODO (Phase 1):
# class BaseAgent(ABC):
#     @abstractmethod
#     async def run(self, request: AgentInputEnvelope) -> AgentOutputEnvelope: ...
