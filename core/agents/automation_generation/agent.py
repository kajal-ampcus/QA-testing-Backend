"""
Agent 5 — Automation Generation.

Turns approved test cases and their matching application map into a
Playwright TypeScript Page Object suite. Selectors are chosen by
selector_strategy.py. Templates are deterministic; this agent does not ask
a model to invent locators or assertions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from core.agents.automation_generation.registry import writer_for
from core.agents.automation_generation.suite import (
    CaseInput,
    StateInput,
    SuitePlan,
)
from core.agents.base import BaseAgent
from schemas.envelope import AgentInputEnvelope, AgentOutputEnvelope, AgentRunStatus


class AutomationGenerationAgent(BaseAgent[SuitePlan]):
    name = "automation_generation"

    async def run(self, request: AgentInputEnvelope) -> SuitePlan:
        payload = request.payload
        cases = [_case(item) for item in payload.get("cases", [])]
        states = {
            code: StateInput(
                state_code=code,
                url_pattern=str(item.get("url_pattern") or ""),
                elements=list(item.get("elements") or []),
            )
            for code, item in (payload.get("states") or {}).items()
        }
        language = str(payload.get("language") or "typescript")
        framework = str(payload.get("framework") or "playwright")
        write = writer_for(language, framework)
        return write(
            suite_dir=Path(request.constraints["suite_dir"]),
            generation_id=UUID(str(payload["generation_id"])),
            project_id=request.project_id,
            application_url=payload.get("application_url"),
            cases=cases,
            states=states,
            incremental=bool(request.constraints.get("incremental")),
        )

    @staticmethod
    def envelope(request: AgentInputEnvelope, plan: SuitePlan, errors: list[str] | None = None) -> AgentOutputEnvelope:
        blocked = [item.tc_code for item in plan.scripts if item.blocked]
        return AgentOutputEnvelope(
            agent_run_id=request.agent_run_id,
            status=AgentRunStatus.PARTIAL if blocked else AgentRunStatus.SUCCESS,
            errors=errors or [],
            decisions=[],
        )


def _case(item: dict[str, Any]) -> CaseInput:
    return CaseInput(
        test_case_id=UUID(str(item["test_case_id"])),
        tc_code=str(item["tc_code"]),
        version=int(item["version"]),
        title=str(item.get("title") or item["tc_code"]),
        requirement_id=UUID(str(item["requirement_id"])),
        requirement_version=int(item["requirement_version"]),
        application_map_id=UUID(str(item["application_map_id"])),
        application_map_version=int(item["application_map_version"]),
        project_id=UUID(str(item["project_id"])),
        steps=list(item.get("steps") or []),
        test_data=dict(item.get("test_data") or {}),
        expected_result=str(item.get("expected_result") or ""),
        credential_ref=item.get("credential_ref"),
        preconditions=list(item.get("preconditions") or []),
        category=str(item.get("category") or ""),
        objective=str(item.get("objective") or ""),
    )
