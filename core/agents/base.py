"""
BaseAgent — every agent implements `run(AgentInputEnvelope) -> AgentOutputEnvelope`
(companion doc Part 1). The Orchestrator (core/orchestrator/, still a stub —
Milestone 1's requirements router plays that role manually for now) is meant
to call every agent through this one method, so it never needs agent-specific
branching logic.
"""

from abc import ABC, abstractmethod

from schemas.envelope import AgentInputEnvelope


class BaseAgent[ResultT](ABC):
    name: str

    @abstractmethod
    async def run(self, request: AgentInputEnvelope) -> ResultT:
        ...
