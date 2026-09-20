"""
Agent 2 — Application Discovery Agent (architecture doc Section 6, 9).

Orchestration: resolve requirement keywords → run crawler → persist each
discovered state INCREMENTALLY (committed per-state) → report summary.

On failure or partial completion, the agent captures structured diagnostic
evidence (login error, screenshot, console errors, network errors, failed
actions) and persists it to application_maps.diagnostic_evidence so the UI
can display a developer-friendly report rather than a blank error state.
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
from infra.secrets.vault_client import get_login_secret
from schemas.envelope import (
    AgentArtifactRef,
    AgentDecision,
    AgentInputEnvelope,
    AgentOutputEnvelope,
    AgentRunStatus,
)


def _extract_keywords(title: str, description: str, domain_tags: list[str]) -> list[str]:
    words = set(domain_tags)
    for text in (title, description):
        words.update(w.strip(".,!?").lower() for w in text.split() if len(w) > 3)
    return list(words)


def _human_termination(reason: str | None) -> str:
    return {
        "EXPLORATION_EXHAUSTED": "All reachable states were discovered.",
        "MAX_PAGES_REACHED": "Discovery stopped at the page limit. Increase max_pages to explore more.",
        "MAX_DURATION_REACHED": "Discovery stopped at the time limit. Increase max_duration_seconds.",
        "MAX_DEPTH_REACHED": "Discovery stopped at the depth limit. Increase max_depth.",
        "ACTION_FAILURES": "Some navigation actions failed. See failed_actions in coverage for details.",
        "UNEXPLORED_ACTIONS": "Some actions were skipped (destructive or form submissions).",
        "AUTHENTICATION_FAILED": "The crawler could not log in. Check credentials and login selectors.",
        "AUTHENTICATION_OR_CRAWL_ERROR": "An error occurred during authentication or crawling.",
    }.get(reason or "", reason or "Unknown reason.")


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

    async def _resolve_keywords(
        self, project_id: uuid.UUID, focus_requirements: list[str]
    ) -> list[str]:
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
                raise ValueError(
                    f"Focus requirement is not approved/current for this project: {req_ref}"
                )
            keywords.extend(
                _extract_keywords(version.title, version.description, version.domain_tags)
            )
        return keywords

    async def _get_login_url(self, credential_ref: str | None, base_url: str) -> str:
        if not credential_ref:
            return base_url
        try:
            secret = await get_login_secret(credential_ref)
            return secret.get("login_url", base_url)
        except Exception:
            return base_url

    async def run(self, request: AgentInputEnvelope) -> AgentOutputEnvelope:
        payload = DiscoveryPayload.model_validate(request.payload)
        keywords = await self._resolve_keywords(request.project_id, payload.focus_requirements)

        login_url = await self._get_login_url(payload.target.credential_ref, payload.target.url)

        app_map = await self._map_repo.create(
            project_id=request.project_id, base_url=payload.target.url
        )
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
                evidence_ref=state.get("evidence_ref"),
            )
            await self._map_repo.session.commit()

        status = "FAILED"
        error_message: str | None = None
        crawler: Crawler | None = None

        # ── Diagnostic evidence collected during the crawl ────────────────
        # Captured from the crawler and client on failure or partial result.
        # Saved to application_maps.diagnostic_evidence so the UI can show
        # a developer-friendly report (login error, screenshot, console/network).
        diagnostic: dict[str, Any] = {
            "auth_attempted": bool(payload.target.credential_ref),
            "auth_succeeded": False,
            "login_error": None,
            "screenshot_ref": None,
            "console_errors": [],
            "network_errors": [],
            "failed_actions": [],
            "termination_detail": None,
        }

        try:
            async with self._tool_gateway.chrome_devtools(
                self.name, payload.target.url, payload.target.credential_ref
            ) as client:
                crawler = Crawler(
                    client=client,
                    budget=budget,
                    keywords=keywords,
                    login_url=login_url,
                    authenticate=bool(payload.target.credential_ref),
                )
                status = await crawler.crawl(payload.target.url, _on_state_discovered)

                # ── Capture diagnostic signals after crawl ends ──────────
                # Whether success or failure — always capture for the record.
                try:
                    screenshot_path = await client.take_screenshot()
                    diagnostic["screenshot_ref"] = screenshot_path
                except Exception:
                    pass

                try:
                    from core.agents.application_discovery.crawler import (
                        _parse_console_messages,
                        _parse_network_requests,
                    )

                    raw_console = await client.list_console_messages()
                    all_msgs = _parse_console_messages(raw_console)
                    diagnostic["console_errors"] = [
                        m for m in all_msgs if m["level"] in ("error", "warning")
                    ]
                except Exception:
                    pass

                try:
                    raw_net = await client.list_network_requests()
                    from core.agents.application_discovery.crawler import (
                        _parse_network_requests,
                    )

                    all_requests = _parse_network_requests(raw_net)
                    # Only record non-2xx responses as diagnostic signals
                    diagnostic["network_errors"] = [
                        r
                        for r in all_requests
                        if r.get("status", "200")
                        not in ("200", "201", "204", "301", "302", "304", "unknown")
                    ]
                except Exception:
                    pass

                # Pull failed actions out of crawler coverage
                if crawler:
                    coverage = getattr(crawler, "coverage", {})
                    diagnostic["failed_actions"] = coverage.get("failed_actions", [])
                    auth_explored = coverage.get("authenticated_explored", False)
                    diagnostic["auth_succeeded"] = auth_explored

        except Exception as exc:
            status = "FAILED"
            error_message = str(exc)
            # Classify the error type for the UI
            msg_lower = error_message.lower()
            if any(
                k in msg_lower
                for k in (
                    "login",
                    "auth",
                    "credential",
                    "password",
                    "username",
                    "submit",
                    "captcha",
                    "sign in",
                    "log in",
                )
            ):
                diagnostic["login_error"] = error_message
                diagnostic["termination_detail"] = (
                    "Authentication failed. The crawler could not log in to the application. "
                    "Check your stored credentials and login selectors."
                )
            else:
                diagnostic["termination_detail"] = (
                    f"Discovery failed with an unexpected error: {error_message}"
                )

        termination_reason = getattr(crawler, "termination_reason", None)
        if status == "FAILED" and error_message is None and crawler is not None:
            failures = crawler.coverage.get("failed_actions", [])
            if failures:
                error_message = (
                    "Discovery could not persist an application state. "
                    f"First failure: {failures[0].get('error', 'unknown error')}: "
                    f"{failures[0].get('detail', 'no detail available')}"
                )
        if status == "FAILED" and not termination_reason:
            termination_reason = "AUTHENTICATION_OR_CRAWL_ERROR"

        # Set human-readable termination detail if not already set
        if not diagnostic["termination_detail"]:
            diagnostic["termination_detail"] = _human_termination(termination_reason)

        # ── Only save diagnostic evidence on non-complete maps ────────────
        # For COMPLETE maps the coverage field is enough. For FAILED/PARTIAL
        # the diagnostic panel in the UI needs the extra detail.
        save_diagnostic = diagnostic if status in ("FAILED", "PARTIAL") else None

        await self._map_repo.set_status(
            app_map.id,
            status,
            termination_reason,
            getattr(crawler, "coverage", None),
            diagnostic_evidence=save_diagnostic,
        )
        await self._map_repo.session.commit()

        decision = AgentDecision(
            decision=(
                f"Discovered {state_count} application state(s), status={status}, "
                f"termination_reason={termination_reason}"
            ),
            reason="Two-phase crawl: unauthenticated pages first, then authenticated pages",
            evidence=[f"{state_count} states persisted to application_map {app_map.id}"],
            confidence=0.9 if status == "COMPLETE" else (0.6 if status == "PARTIAL" else 0.1),
            source=EvidenceSource.OBSERVED_DOM,
        )

        run_status = {
            "COMPLETE": AgentRunStatus.SUCCESS,
            "PARTIAL": AgentRunStatus.PARTIAL,
        }.get(status, AgentRunStatus.FAILED)

        return AgentOutputEnvelope(
            agent_run_id=request.agent_run_id,
            status=run_status,
            artifacts=[
                AgentArtifactRef(
                    type="application_map", id=str(app_map.id), version=app_map.version
                )
            ],
            decisions=[decision],
            requires_human_approval=False,
            errors=[error_message] if error_message else [],
        )
