"""
Agent 2 — Application Discovery Agent (architecture doc Section 6, 9).

Orchestration: resolve requirement keywords → run crawler → persist each
discovered state INCREMENTALLY (committed per-state) → report summary.

On failure or partial completion, the agent captures structured diagnostic
evidence (login error, screenshot, console errors, network errors, failed
actions) and persists it to application_maps.diagnostic_evidence so the UI
can display a developer-friendly report rather than a blank error state.
"""

import os
import re
import uuid
from typing import Any
from urllib.parse import urljoin

from core.agents.application_discovery.crawler import CrawlBudget
from core.agents.application_discovery.evidence import evidence_sha256
from core.agents.application_discovery.parallel_crawler import ParallelCrawler
from core.agents.application_discovery.schemas import DiscoveryPayload
from core.agents.base import BaseAgent
from core.tool_gateway.gateway import ToolGateway
from core.tool_gateway.secret_resolver import resolve_login_url
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


def _screen_index(app_map: Any) -> list[dict[str, Any]]:
    if app_map is None:
        return []
    graph = (app_map.coverage or {}).get("app_flow_graph") or {}
    nodes = {
        node.get("fingerprint"): node
        for node in graph.get("nodes") or []
        if isinstance(node, dict)
    }
    return [
        {
            "fingerprint": state.fingerprint,
            "url_pattern": state.url_pattern,
            "page_key": (nodes.get(state.fingerprint) or {}).get("functional_key") or state.url_pattern,
            "functional_key": (nodes.get(state.fingerprint) or {}).get("functional_key") or state.url_pattern,
            "evidence_ref": state.evidence_ref,
        }
        for state in app_map.states
    ]


def _extract_keywords(title: str, description: str, domain_tags: list[str]) -> list[str]:
    words = set(domain_tags)
    for text in (title, description):
        words.update(w.strip(".,!?").lower() for w in text.split() if len(w) > 3)
    return list(words)


def _login_failure_detail(actions: list[dict[str, Any]]) -> str | None:
    """Return the crawler message from a failed sign-in, without page snapshots."""
    for failure in actions:
        detail = str(failure.get("detail") or "")
        if re.search(
            r"authentication failed|could not be read|sign-in page|login form",
            detail,
            re.I,
        ):
            return detail.split("Page text snippet", 1)[0].strip() or detail
    return None


def _human_termination(reason: str | None) -> str:
    return {
        "EXPLORATION_EXHAUSTED": "All reachable states were discovered.",
        "AWAITING_BRANCH_SELECTION": "Selected pages were inspected. Choose another saved path or generate tests from the observed scope.",
        "MAX_PAGES_REACHED": "Discovery stopped at the page limit. Increase max_pages to explore more.",
        "MAX_DURATION_REACHED": "Discovery stopped at the time limit. Increase max_duration_seconds.",
        "MAX_DEPTH_REACHED": "Discovery stopped at the depth limit. Increase max_depth.",
        "ACTION_FAILURES": "Some navigation actions failed. See failed_actions in coverage for details.",
        "UNEXPLORED_ACTIONS": "Some actions were skipped (destructive or form submissions).",
        "INPUT_REQUIRED": (
            "Discovery found fields it cannot fill. Enter the requested values, then continue."
        ),
        "AUTHENTICATION_FAILED": "The crawler could not log in. Check credentials and login selectors.",
        "AUTHENTICATION_OR_CRAWL_ERROR": "An error occurred during authentication or crawling.",
        "CANCELLED_BY_USER": "Discovery was stopped by the user.",
        "SAFETY_LIMIT_REACHED": "Automatic discovery reached an internal safety circuit breaker.",
        "WORKER_FAILURE": "The discovery worker stopped unexpectedly. Continue from the saved checkpoint.",
        "SECURITY_VERIFICATION_REQUIRED": (
            "Discovery paused because the target requires interactive security verification. "
            "Allowlist the discovery worker or disable the challenge for the test/staging "
            "hostname, then continue from the saved checkpoint."
        ),
    }.get(reason or "", reason or "Unknown reason.")


_WORKER_TIMEOUT_MARGIN_SECONDS = 60


def _duration_within_worker_timeout(requested_seconds: int) -> int:
    """The worker cancels a job at WORKER_JOB_TIMEOUT. Stopping the crawl first
    lets it finish normally with MAX_DURATION_REACHED and a saved checkpoint."""
    try:
        worker_timeout = int(os.environ.get("WORKER_JOB_TIMEOUT", "1200"))
    except ValueError:
        worker_timeout = 1200
    ceiling = max(1, worker_timeout - _WORKER_TIMEOUT_MARGIN_SECONDS)
    return max(1, min(requested_seconds, ceiling))


def _select_auth_flow(
    checkpoint: dict[str, Any] | None, requested_flow_id: str | None
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve an optional flow choice without blocking credential-based login.

    A fresh/scratch run has no catalog yet, and a single-role application does
    not need the tester to make a second selection after choosing an account.
    Multiple discovered flows remain selectable, but absence of a selection
    falls back to the saved credential's login URL.
    """
    available = {
        flow["id"]: flow
        for flow in (checkpoint or {}).get("authentication_flows", [])
        if isinstance(flow, dict) and flow.get("id")
    }
    selected_id = requested_flow_id
    if not selected_id and len(available) == 1:
        selected_id = next(iter(available))
    return selected_id, available.get(selected_id or "")


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
        # Set as soon as this run owns a map, so failure handling updates the
        # map this run was writing rather than whichever map is newest.
        self.application_map_id: uuid.UUID | None = None

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

    async def run(self, request: AgentInputEnvelope) -> AgentOutputEnvelope:
        payload = DiscoveryPayload.model_validate(request.payload)
        keywords = await self._resolve_keywords(request.project_id, payload.focus_requirements)

        login_url = await resolve_login_url(
            payload.target.credential_ref, request.project_id, payload.target.url
        )

        checkpoint: dict[str, Any] | None = None
        if payload.resume_application_map_id:
            app_map = await self._map_repo.get_with_states(
                uuid.UUID(payload.resume_application_map_id)
            )
            if app_map is None or app_map.project_id != request.project_id:
                raise ValueError("Discovery checkpoint not found")
            if app_map.status not in {"PARTIAL", "COMPLETE"} or not app_map.discovery_checkpoint:
                raise ValueError("Discovery is not resumable")
            checkpoint = app_map.discovery_checkpoint
            graph = checkpoint.setdefault("graph", {})
            checkpoint_nodes = {
                node["fingerprint"]: node for node in graph.setdefault("nodes", [])
            }
            for persisted_state in app_map.states:
                checkpoint_nodes.setdefault(
                    persisted_state.fingerprint,
                    {
                        "fingerprint": persisted_state.fingerprint,
                        "url_pattern": persisted_state.url_pattern,
                        "area_id": "unknown",
                    },
                )
            graph["nodes"] = list(checkpoint_nodes.values())
            app_map.status = "RUNNING"
            app_map.termination_reason = None
        elif payload.start_from_scratch or payload.discovery_scope.mode == "guided":
            # A user-requested scratch run creates a new canonical map version
            # with no inherited nodes, graph, catalog, or checkpoint frontier.
            # The previous version remains available as history, but it cannot
            # leak states into this run.
            app_map = await self._map_repo.create(
                project_id=request.project_id, base_url=payload.target.url
            )
        else:
            # Every separate run contributes to the project's canonical map.
            # Ordinary non-resume runs incrementally retain the combined graph.
            app_map = await self._map_repo.get_latest_for_project(request.project_id)
            if app_map is None:
                app_map = await self._map_repo.create(
                    project_id=request.project_id, base_url=payload.target.url
                )
            else:
                previous_graph = (app_map.coverage or {}).get("app_flow_graph", {})
                previous_catalog = (app_map.coverage or {}).get(
                    "discovery_catalog", {}
                )
                checkpoint = {
                    "version": 1,
                    "jobs": [],
                    "completed_nodes": [],
                    "graph": {
                        "nodes": list(previous_graph.get("nodes", [])),
                        "edges": list(previous_graph.get("edges", [])),
                        "subgraphs": list(previous_graph.get("subgraphs", [])),
                    },
                    "modules": list(previous_catalog.get("modules", [])),
                    "authentication_flows": list(
                        previous_catalog.get("authentication_flows", [])
                    ),
                    "module_inventory_flows": list(
                        previous_catalog.get("module_inventory_flows", [])
                    ),
                }
                checkpoint_nodes = {
                    node["fingerprint"]: node
                    for node in checkpoint["graph"]["nodes"]
                }
                for persisted_state in app_map.states:
                    checkpoint_nodes.setdefault(
                        persisted_state.fingerprint,
                        {
                            "fingerprint": persisted_state.fingerprint,
                            "url_pattern": persisted_state.url_pattern,
                            "area_id": "unknown",
                        },
                    )
                checkpoint["graph"]["nodes"] = list(checkpoint_nodes.values())
                app_map.status = "RUNNING"
                app_map.termination_reason = None
        self.application_map_id = app_map.id
        await self._map_repo.session.commit()
        fresh_version = payload.start_from_scratch or payload.discovery_scope.mode == "guided"
        if fresh_version and app_map.version > 1:
            baseline = await self._map_repo.get_by_version(request.project_id, app_map.version - 1)
        else:
            baseline = await self._map_repo.get_with_states(app_map.id)
        previous_screens = _screen_index(baseline)
        previous_dropdowns = list((baseline.coverage or {}).get("dropdowns") or []) if baseline is not None else []

        automatic_limits = payload.crawl_budget.automatic_limits
        budget = CrawlBudget(
            max_pages=(
                int(os.environ.get("DISCOVERY_AUTO_MAX_STATES", "10000"))
                if automatic_limits else payload.crawl_budget.max_pages
            ),
            max_depth=(
                int(os.environ.get("DISCOVERY_AUTO_MAX_DEPTH", "50"))
                if automatic_limits else payload.crawl_budget.max_depth
            ),
            max_duration_seconds=_duration_within_worker_timeout(
                int(os.environ.get("DISCOVERY_AUTO_MAX_DURATION_SECONDS", "21600"))
                if automatic_limits else payload.crawl_budget.max_duration_seconds
            ),
            automatic_limits=automatic_limits,
        )

        # Newly inserted maps do not have this async relationship loaded.
        # An explicit query avoids implicit lazy loading (MissingGreenlet).
        state_count = await self._map_repo.count_states(app_map.id)

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
                evidence_sha256=evidence_sha256(state.get("evidence_ref")),
            )
            await self._map_repo.session.commit()

        async def _on_checkpoint(checkpoint_value: dict[str, Any]) -> None:
            checkpoint_value["configuration"]["credential_ref"] = payload.target.credential_ref
            await self._map_repo.set_checkpoint(app_map.id, checkpoint_value)
            await self._map_repo.session.commit()

        status = "FAILED"
        error_message: str | None = None
        crawler: ParallelCrawler | None = None

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
            "security_verification_required": False,
            "termination_detail": None,
        }

        try:
            saved_config = (checkpoint or {}).get("configuration", {})
            discovery_mode = saved_config.get("mode", payload.discovery_scope.mode)
            selected_auth_flow = saved_config.get(
                "selected_auth_flow", payload.discovery_scope.selected_auth_flow
            )
            selected_auth_flows = saved_config.get(
                "selected_auth_flows", payload.discovery_scope.selected_auth_flows
            ) or ([selected_auth_flow] if selected_auth_flow else [])
            selected_auth_flows = list(dict.fromkeys(selected_auth_flows))
            available_auth_flows = {
                flow["id"]: flow
                for flow in (checkpoint or {}).get("authentication_flows", [])
            }
            selected_flows = [
                available_auth_flows[flow_id]
                for flow_id in selected_auth_flows
                if flow_id in available_auth_flows
            ]
            selected_login_flow = next(
                (flow for flow in selected_flows if flow.get("kind") == "login"),
                None,
            )
            if discovery_mode == "complete" and selected_login_flow:
                selected_auth_flow = selected_login_flow["id"]
            selected_flow = available_auth_flows.get(selected_auth_flow or "")
            auth_entry_url = (
                urljoin(payload.target.url, selected_flow["url_pattern"])
                if selected_flow
                else login_url
            )
            auth_entry_urls = [
                urljoin(payload.target.url, flow["url_pattern"])
                for flow in selected_flows
            ]
            needs_authenticated_session = discovery_mode in {"modules", "deep"} or (
                discovery_mode == "complete" and bool(selected_auth_flows)
            )
            if needs_authenticated_session and not payload.target.credential_ref:
                raise ValueError(
                    "The selected authentication flow requires a saved test account."
                )
            crawler = ParallelCrawler(
                    client_factory=lambda: self._tool_gateway.chrome_devtools(
                        self.name,
                        payload.target.url,
                        payload.target.credential_ref,
                        request.project_id,
                    ),
                    budget=budget,
                    keywords=keywords,
                    login_url=login_url,
                    authenticate=(
                        bool(payload.target.credential_ref)
                        and discovery_mode in {"guided", "modules", "deep", "complete", "targeted", "full"}
                    ),
                    worker_limit=payload.crawl_budget.worker_limit,
                    discovery_mode=discovery_mode,
                    selected_auth_flow=selected_auth_flow,
                    selected_auth_flows=selected_auth_flows,
                    auth_entry_url=auth_entry_url,
                    auth_entry_urls=auth_entry_urls,
                    selected_areas=saved_config.get(
                        "selected_areas", payload.discovery_scope.selected_areas
                    ),
                    selected_modules=saved_config.get(
                        "selected_modules", payload.discovery_scope.selected_modules
                    ),
                    selected_branches=payload.discovery_scope.selected_branches,
                    checkpoint=checkpoint,
                    on_checkpoint=_on_checkpoint,
                    project_id=request.project_id,
                    credential_ref=payload.target.credential_ref,
                    previous_screens=previous_screens,
                    previous_dropdowns=previous_dropdowns,
                )
            status = await crawler.crawl(payload.target.url, _on_state_discovered)
            coverage = crawler.coverage
            diagnostic["failed_actions"] = coverage.get("failed_actions", [])
            diagnostic["screenshot_ref"] = next(
                (
                    failure.get("screenshot_ref")
                    for failure in diagnostic["failed_actions"]
                    if failure.get("screenshot_ref")
                ),
                None,
            )
            diagnostic["auth_succeeded"] = coverage.get("authenticated_explored", False)
            diagnostic["auth_attempted"] = (
                diagnostic["auth_succeeded"] if discovery_mode == "guided"
                else bool(getattr(crawler, "_authenticate", False))
            )
            if diagnostic["auth_attempted"] and not diagnostic["auth_succeeded"]:
                login_error = _login_failure_detail(diagnostic["failed_actions"])
                if login_error and not diagnostic["login_error"]:
                    diagnostic["login_error"] = login_error
                if not diagnostic["termination_detail"]:
                    diagnostic["termination_detail"] = (
                        "Login was attempted and did not succeed, so pages after "
                        "login were not discovered."
                    )
            security_failure = next(
                (
                    failure
                    for failure in diagnostic["failed_actions"]
                    if failure.get("error") == "SecurityVerificationRequiredError"
                ),
                None,
            )
            if security_failure:
                diagnostic["auth_attempted"] = False
                diagnostic["security_verification_required"] = True
                diagnostic["termination_detail"] = security_failure.get(
                    "detail", _human_termination("SECURITY_VERIFICATION_REQUIRED")
                )
            readiness_failure = next(
                (
                    failure
                    for failure in diagnostic["failed_actions"]
                    if failure.get("error") == "ApplicationReadinessTimeoutError"
                ),
                None,
            )
            if readiness_failure:
                diagnostic["termination_detail"] = readiness_failure.get(
                    "detail",
                    "The target hosting service did not finish waking before discovery timed out.",
                )

        except Exception as exc:
            status = "PARTIAL"
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
        if termination_reason == "INPUT_REQUIRED":
            diagnostic["login_error"] = None
            diagnostic["termination_detail"] = _human_termination("INPUT_REQUIRED")
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

        # Keep the login failure even when the public crawl finishes.
        # A COMPLETE map otherwise hides the fact that sign-in never succeeded.
        auth_failed = bool(diagnostic.get("auth_attempted")) and not diagnostic.get(
            "auth_succeeded"
        )
        save_diagnostic = (
            diagnostic if status in ("FAILED", "PARTIAL") or auth_failed else None
        )

        await self._map_repo.set_status(
            app_map.id,
            status,
            termination_reason,
            getattr(crawler, "coverage", None),
            diagnostic_evidence=save_diagnostic,
            replace_diagnostic=True,
        )
        await self._map_repo.session.commit()

        decision = AgentDecision(
            decision=(
                f"Discovered {state_count} application state(s), status={status}, "
                f"termination_reason={termination_reason}"
            ),
            reason=(
                "Application-agnostic staged discovery: entry points, authenticated modules, "
                "then selected scope"
            ),
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
            requires_human_approval=(
                termination_reason == "SECURITY_VERIFICATION_REQUIRED"
            ),
            errors=[error_message] if error_message else [],
        )
