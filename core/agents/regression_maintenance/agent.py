"""
Agent 11 — Regression & Maintenance. Diffs two application-map graphs and
returns a literal OrchestratorState re-entry value.
"""

from core.agents.base import BaseAgent
from core.agents.regression_maintenance.diffing import diff_application_maps
from schemas.envelope import AgentInputEnvelope


class RegressionMaintenanceAgent(BaseAgent[dict]):
    name = "regression_maintenance"

    async def run(self, request: AgentInputEnvelope) -> dict:
        return diff_application_maps(
            request.payload.get("previous_graph") or {},
            request.payload.get("current_graph") or {},
        )
