"""
Agent 2 — Application Discovery Agent (architecture doc Section 6, 9).

The crawl itself is fully deterministic (crawler.py) — this agent's job is
orchestration: resolve requirement keywords, run the crawler, persist each
discovered state INCREMENTALLY (committed per-state, not in one giant
transaction at the end) so a long crawl's progress survives a crash partway
through, run the crawl inside chrome-devtools-mcp with the target's domain as
--allowedUrlPattern, and report a summary envelope.

Every discovered element carries source=OBSERVED_DOM — nothing here is
invented. A destructive-looking element is recorded but never auto-clicked
(Section 29, enforced inside crawler.py, not re-checked here).
"""

import uuid
from typing import Any

from core.agents.application_discovery.crawler import CrawlBudget, Crawler
from core.agents.application_discovery.schemas import DiscoveryPayload
from core.agents.base import BaseAgent
from core.tool_gateway.gateway import ToolGateway
from domain.enums import EvidenceSource, RequirementStatus
from infra.db.repositories.application_map_repo import ApplicationMapRepository
from infra.db.repositories.requirement_repo import RequirementRepository
from schemas.envelope import (
    AgentArtifactRef,
    AgentDecision,
    AgentInputEnvelope,
    AgentOutputEnvelope,
    AgentRunStatus,
)


def _extract_keywords_from_requirement(title: str, description: str, domain_tags: list[str]) -> list[str]:
    """Deterministic — no LLM call. domain_tags are already curated by the
    Requirement Understanding Agent; title/description words are added as a
    broader (noisier) fallback so a crawl still has something to score
    against even if domain_tags came back empty for a given requirement."""
    words = set(domain_tags)
    for text in (title, description):
        words.update(w.strip(".,!?").lower() for w in text.split() if len(w) > 3)
    return list(words)


class ApplicationDiscoveryAgent(BaseAgent[AgentOutputEnvelope]):
    name = "application_discovery"

    def __init__(
        self,
        map_repo: ApplicationMapRepository,
        requirement_repo: RequirementRepository | None = None,
        tool_gateway: ToolGateway | None = None,
    ) -> None:
        self._map_repo = map_repo
        self._requirement_repo = requirement_repo
        self._tool_gateway = tool_gateway or ToolGateway()

    async def _resolve_keywords(self, project_id: uuid.UUID, focus_requirements: list[str]) -> list[str]:
        if self._requirement_repo is None:
            return []
        keywords: list[str] = []
        for req_ref in focus_requirements:
            req_id_str, _, version_ref = req_ref.partition("@v")
            try:
                req_uuid = uuid.UUID(req_id_str)
            except ValueError as exc:
                raise ValueError(f"Invalid focus requirement reference: {req_ref}") from exc
            pair = await self._requirement_repo.get_with_current_version(req_uuid)
            if pair is None:
                raise ValueError(f"Unknown focus requirement: {req_ref}")
            requirement, version = pair
            if (
                requirement.project_id != project_id
                or requirement.status != RequirementStatus.APPROVED
                or (version_ref and version_ref != str(version.version))
            ):
                raise ValueError(f"Focus requirement is not approved/current for this project: {req_ref}")
            keywords.extend(
                _extract_keywords_from_requirement(version.title, version.description, version.domain_tags)
            )
        return keywords

    async def run(self, request: AgentInputEnvelope) -> AgentOutputEnvelope:
        payload = DiscoveryPayload.model_validate(request.payload)
        keywords = await self._resolve_keywords(request.project_id, payload.focus_requirements)

        app_map = await self._map_repo.create(project_id=request.project_id, base_url=payload.target.url)
        await self._map_repo.session.commit()

        budget = CrawlBudget(
            max_pages=payload.crawl_budget.max_pages,
            max_depth=payload.crawl_budget.max_depth,
            max_duration_seconds=payload.crawl_budget.max_duration_seconds,
        )

        state_count = 0

        async def _on_state_discovered(state: dict[str, Any]) -> None:
            nonlocal state_count
            state_count += 1
            await self._map_repo.add_state(
                application_map_id=app_map.id,
                state_code=f"STATE-{state_count:03d}",
                url_pattern=state["url_pattern"],
                fingerprint=state["fingerprint"],
                reached_via=state["reached_via"],
                elements=state["elements"],
            )
            # Committed per-state, deliberately — see module docstring.
            await self._map_repo.session.commit()

        status = "FAILED"
        error_message: str | None = None
        try:
            async with self._tool_gateway.chrome_devtools(self.name, payload.target.url) as client:
                status = await Crawler(client, budget, keywords).crawl(payload.target.url, _on_state_discovered)
        except Exception as exc:  # noqa: BLE001 — intentional: this runs inside a background
            # worker task (apps/worker/tasks/run_discovery.py). An unhandled exception here
            # must not crash the worker — it must mark the map FAILED with a reason so a
            # human sees it in the Agent Activity feed (Section 27), not lose the run silently.
            status = "FAILED"
            error_message = str(exc)

        await self._map_repo.set_status(app_map.id, status)
        await self._map_repo.session.commit()

        decision = AgentDecision(
            decision=f"Discovered {state_count} application state(s), status={status}",
            reason="Deterministic priority-queue crawl via chrome-devtools-mcp (Section 9)",
            evidence=[f"{state_count} states persisted to application_map {app_map.id}"],
            confidence=0.9 if status == "COMPLETE" else 0.6,
            source=EvidenceSource.OBSERVED_DOM,
        )

        run_status = {
            "COMPLETE": AgentRunStatus.SUCCESS,
            "PARTIAL": AgentRunStatus.PARTIAL,
        }.get(status, AgentRunStatus.FAILED)

        return AgentOutputEnvelope(
            agent_run_id=request.agent_run_id,
            status=run_status,
            artifacts=[AgentArtifactRef(type="application_map", id=str(app_map.id), version=app_map.version)],
            decisions=[decision],
            requires_human_approval=False,  # Section 21 — review recommended, not a blocking gate for this agent
            errors=[error_message] if error_message else [],
        )
