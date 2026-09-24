"""Bounded parallel discovery built on the existing deterministic Crawler.

Each pool worker owns one isolated Chrome DevTools MCP process. Workers share
only crawl coordination state (frontier, deduplication and flow graph); browser
pages and authentication cookies never leak between worker contexts.
"""

import asyncio
import itertools
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, AsyncContextManager
from urllib.parse import urldefrag, urljoin, urlparse

from core.agents.application_discovery.crawler import (
    ClickStep,
    CrawlBudget,
    Crawler,
    _is_external,
    _parse_elements,
    _relevance_score,
)
from core.policy_safety.destructive_action_lexicon import classify_risk
from core.tool_gateway.gateway import BrowserInspection
from domain.enums import RiskLevel


ClientFactory = Callable[[], AsyncContextManager[BrowserInspection]]
OnStateDiscovered = Callable[[dict[str, Any]], Awaitable[None]]
OnCheckpoint = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(order=True)
class DiscoveryJob:
    priority: float
    sequence: int
    path: list[ClickStep] = field(compare=False, default_factory=list)
    skip_auth: bool = field(compare=False, default=False)
    parent_fingerprint: str | None = field(compare=False, default=None)
    module_id: str | None = field(compare=False, default=None)
    force_expand: bool = field(compare=False, default=False)


class ParallelCrawler:
    """Dynamic shared-frontier crawler with a bounded isolated-browser pool."""

    def __init__(
        self,
        client_factory: ClientFactory,
        budget: CrawlBudget,
        keywords: list[str],
        login_url: str | None,
        authenticate: bool,
        worker_limit: int = 3,
        discovery_mode: str = "complete",
        selected_areas: list[str] | None = None,
        selected_modules: list[str] | None = None,
        checkpoint: dict[str, Any] | None = None,
        on_checkpoint: OnCheckpoint | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._budget = budget
        self._keywords = keywords
        self._login_url = login_url
        self._authenticate = authenticate
        self._worker_limit = max(1, min(worker_limit, 5))
        self._discovery_mode = discovery_mode
        self._selected_areas = {value.lower() for value in (selected_areas or [])}
        self._selected_modules = {value.lower() for value in (selected_modules or [])}
        self._areas: dict[str, dict[str, Any]] = {}
        self._modules: dict[str, dict[str, Any]] = {}
        self._area_roots: dict[str, str] = {}
        self._checkpoint = checkpoint or {}
        self._on_checkpoint = on_checkpoint
        self._job_states: dict[str, dict[str, Any]] = {}
        self._seen: set[str] = set()
        self._expanded: set[str] = set()
        self._expanding: set[str] = set()
        self._queued_actions: set[tuple[str, str, str, str]] = set()
        self._failures: list[dict[str, str]] = []
        self._skipped: set[str] = set()
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: set[tuple[str, str, str]] = set()
        self._lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._sequence = itertools.count()
        self._started = 0.0
        self._actions_examined = 0
        self._depth_limited = 0
        self._pending_at_limit = 0
        self._termination = "EXPLORATION_EXHAUSTED"
        self._authenticated_workers = 0
        self._safety_limit_kind: str | None = None
        self.termination_reason = "EXPLORATION_EXHAUSTED"
        self.coverage: dict[str, Any] = {}
        self._restore_checkpoint()

    @staticmethod
    def _serialize_step(step: ClickStep) -> dict[str, Any]:
        return {
            "role": step.role,
            "name": step.name,
            "url": step.url,
            "value": step.value,
        }

    @classmethod
    def _job_key(cls, job: DiscoveryJob) -> str:
        phase = "public" if job.skip_auth else "authenticated"
        path = "/".join(
            f"{step.role}:{step.name}:{step.url or ''}:{step.value or ''}"
            for step in job.path
        )
        return f"{phase}|{path}|expand={job.force_expand}"

    @classmethod
    def _serialize_job(cls, job: DiscoveryJob, status: str) -> dict[str, Any]:
        return {
            "key": cls._job_key(job),
            "status": status,
            "priority": job.priority,
            "path": [cls._serialize_step(step) for step in job.path],
            "skip_auth": job.skip_auth,
            "parent_fingerprint": job.parent_fingerprint,
            "module_id": job.module_id,
            "force_expand": job.force_expand,
        }

    def _deserialize_job(self, raw: dict[str, Any]) -> DiscoveryJob:
        return DiscoveryJob(
            priority=float(raw.get("priority", 0)),
            sequence=next(self._sequence),
            path=[ClickStep(**step) for step in raw.get("path", [])],
            skip_auth=bool(raw.get("skip_auth")),
            parent_fingerprint=raw.get("parent_fingerprint"),
            module_id=raw.get("module_id"),
            force_expand=bool(raw.get("force_expand")),
        )

    def _restore_checkpoint(self) -> None:
        graph = self._checkpoint.get("graph", {})
        self._nodes = {
            node["fingerprint"]: node for node in graph.get("nodes", [])
        }
        self._seen = set(self._nodes)
        self._edges = {
            (edge["parent_fingerprint"], edge["child_fingerprint"], edge["action"])
            for edge in graph.get("edges", [])
        }
        self._expanded = set(self._checkpoint.get("completed_nodes", []))
        self._areas = {
            area["id"]: area for area in graph.get("subgraphs", [])
        }
        self._area_roots = {
            area_id: area["state_fingerprints"][0]
            for area_id, area in self._areas.items()
            if area.get("state_fingerprints")
        }
        self._modules = {
            module["id"]: module
            for module in self._checkpoint.get("modules", [])
        }
        self._job_states = {
            job["key"]: job for job in self._checkpoint.get("jobs", [])
        }

    def _checkpoint_payload(self) -> dict[str, Any]:
        jobs = list(self._job_states.values())
        return {
            "version": 1,
            "configuration": {
                "mode": self._discovery_mode,
                "selected_areas": sorted(self._selected_areas),
                "selected_modules": sorted(self._selected_modules),
                "max_pages": self._budget.max_pages,
                "max_depth": self._budget.max_depth,
                "max_duration_seconds": self._budget.max_duration_seconds,
                "worker_limit": self._worker_limit,
                "automatic_limits": self._budget.automatic_limits,
            },
            "jobs": jobs,
            "completed_nodes": sorted(self._expanded),
            "pending_nodes": [job for job in jobs if job["status"] == "pending"],
            "failed_nodes": [job for job in jobs if job["status"] == "failed"],
            "in_progress_nodes": [job for job in jobs if job["status"] == "in_progress"],
            "graph": {
                "nodes": list(self._nodes.values()),
                "edges": [
                    {
                        "parent_fingerprint": parent,
                        "child_fingerprint": child,
                        "action": action,
                    }
                    for parent, child, action in sorted(self._edges)
                ],
                "subgraphs": list(self._areas.values()),
            },
            "modules": list(self._modules.values()),
        }

    async def _save_checkpoint(self) -> None:
        if self._on_checkpoint:
            # State inserts and checkpoint updates share one SQLAlchemy
            # session in the worker; serialize both transaction types.
            async with self._persist_lock:
                await self._on_checkpoint(self._checkpoint_payload())

    async def _set_job_status(self, job: DiscoveryJob, status: str) -> None:
        self._job_states[self._job_key(job)] = self._serialize_job(job, status)
        await self._save_checkpoint()

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "area"

    def _classify_state(
        self, state: dict[str, Any], nodes: list[dict[str, Any]], authenticated: bool
    ) -> tuple[str, str]:
        path = state.get("url_pattern", "/").lower()
        root_name = str(
            next(
                (
                    node.get("name", "")
                    for node in nodes
                    if node.get("role") == "RootWebArea"
                ),
                "",
            )
        ).lower()
        identity = path + " " + root_name
        if re.search(r"register|sign.?up|create.?account", identity):
            return "registration", "Registration"
        if re.search(r"forgot|reset.?password|recover", identity):
            return "password-reset", "Password reset"
        if re.search(r"login|log.?in|sign.?in", identity) and not authenticated:
            return "login", "Login"
        if authenticated or re.search(r"dashboard|portal|workspace|home", identity):
            return "dashboard", "Dashboard"
        return "public", "Landing / Public"

    def _catalog_state(
        self,
        fingerprint: str,
        state: dict[str, Any],
        nodes: list[dict[str, Any]],
        authenticated: bool,
        module_id: str | None = None,
    ) -> str:
        area_id, label = self._classify_state(state, nodes, authenticated)
        area = self._areas.setdefault(
            area_id,
            {
                "id": area_id,
                "label": label,
                "kind": (
                    "authenticated"
                    if area_id == "dashboard"
                    else (
                        "authentication"
                        if area_id in {"login", "registration", "password-reset"}
                        else "public"
                    )
                ),
                "state_fingerprints": [],
                "selectable": True,
            },
        )
        is_area_root = not area["state_fingerprints"]
        if fingerprint not in area["state_fingerprints"]:
            area["state_fingerprints"].append(fingerprint)
        if area_id == "dashboard" and is_area_root:
            for node in nodes:
                if node.get("role") not in {"link", "button", "menuitem", "tab"}:
                    continue
                name = str(node.get("name", "")).strip()
                if not name or re.search(r"log.?out|sign.?out|dashboard|home|profile", name, re.I):
                    continue
                module_id = self._slug(name)
                module = self._modules.setdefault(
                    module_id,
                    {
                        "id": module_id,
                        "label": name,
                        "area_id": "dashboard",
                        "state_fingerprints": [],
                    },
                )
                if fingerprint not in module["state_fingerprints"]:
                    module["state_fingerprints"].append(fingerprint)
        if module_id and module_id in self._modules:
            module_fingerprints = self._modules[module_id]["state_fingerprints"]
            if fingerprint not in module_fingerprints:
                module_fingerprints.append(fingerprint)
        return area_id

    def _action_in_scope(
        self, area_id: str, name: str, depth: int, module_id: str | None
    ) -> bool:
        if self._discovery_mode == "complete":
            return True
        if self._discovery_mode == "inventory":
            # Follow only top-level links. This reveals Registration/Public
            # areas without turning the inventory pass into a deep crawl.
            return area_id != "dashboard" and depth == 0
        if area_id not in self._selected_areas and not (
            area_id == "dashboard" and self._selected_modules
        ):
            return False
        if area_id != "dashboard" or not self._selected_modules:
            return True
        if module_id in self._selected_modules:
            return True
        return self._slug(name) in self._selected_modules

    @staticmethod
    def _action_text(step: ClickStep) -> str:
        if step.role == "link" and step.url:
            return f"navigate(url={step.url!r},observed_link={step.name!r})"
        return (
            f"{('fill' if step.value is not None else 'click')}"
            f"(role={step.role},name={step.name!r}"
            + (f",value={step.value!r}" if step.value is not None else "")
            + ")"
        )

    async def _enqueue_children(
        self,
        queue: asyncio.PriorityQueue[DiscoveryJob],
        nodes: list[dict[str, Any]],
        path: list[ClickStep],
        skip_auth: bool,
        parent_fingerprint: str,
        base_url: str,
        area_id: str,
        module_id: str | None,
    ) -> bool:
        if len(path) >= self._budget.max_depth:
            self._depth_limited += 1
            deferred = DiscoveryJob(
                priority=0,
                sequence=next(self._sequence),
                path=path,
                skip_auth=skip_auth,
                module_id=module_id,
                force_expand=True,
            )
            await self._set_job_status(deferred, "pending")
            if self._budget.automatic_limits:
                self._safety_limit_kind = "depth"
            return False
        root = next((node for node in nodes if node.get("role") == "RootWebArea"), {})
        current_url = root.get("url") or base_url
        for element in nodes:
            role, name = element.get("role", ""), element.get("name", "")
            if not self._action_in_scope(area_id, name, len(path), module_id):
                continue
            if role not in {"link", "button", "menuitem", "tab", "radio", "checkbox", "combobox"}:
                continue
            if element.get("disabled") or element.get("visible") is False:
                continue
            destination = element.get("url")
            if destination:
                destination = urljoin(current_url, destination)
                if urlparse(destination).scheme not in {"http", "https"} or _is_external(destination, base_url):
                    continue
                if urldefrag(destination)[0] == urldefrag(current_url)[0] and "#" in destination:
                    continue
            safe_link = role == "link" and bool(destination)
            form_action = bool(re.search(
                r"\b(submit|send|subscribe|save|sign out|log out|logout|sign in|log in|login|register|create account)\b",
                name,
                re.I,
            ))
            if (
                classify_risk(role, name) == RiskLevel.DESTRUCTIVE
                or (form_action and not safe_link)
                or element.get("input_type") == "submit"
            ):
                self._skipped.add(f"{urlparse(current_url).path}: {role} {name}")
                continue
            values = element.get("options", []) if role == "combobox" else [None]
            if role == "combobox" and not values:
                self._skipped.add(f"{urlparse(current_url).path}: combobox {name} (options unavailable)")
            for value in values:
                canonical = urldefrag(destination)[0].rstrip("/") if destination else None
                key = (parent_fingerprint, role, canonical or name, str(value))
                async with self._lock:
                    if key in self._queued_actions:
                        continue
                    self._queued_actions.add(key)
                step = ClickStep(role, name, destination, value)
                child_module = module_id or (
                    self._slug(name)
                    if area_id == "dashboard" and self._slug(name) in self._selected_modules
                    else None
                )
                child_job = DiscoveryJob(
                    priority=-_relevance_score(name, self._keywords),
                    sequence=next(self._sequence),
                    path=[*path, step],
                    skip_auth=skip_auth,
                    parent_fingerprint=parent_fingerprint,
                    module_id=child_module,
                )
                if self._job_key(child_job) in self._job_states:
                    continue
                await queue.put(child_job)
                await self._set_job_status(child_job, "pending")
        return True

    async def _run_phase(
        self,
        base_url: str,
        skip_auth: bool,
        on_state_discovered: OnStateDiscovered,
    ) -> None:
        queue: asyncio.PriorityQueue[DiscoveryJob] = asyncio.PriorityQueue()
        phase_jobs = [
            raw
            for raw in self._job_states.values()
            if bool(raw.get("skip_auth")) == skip_auth
        ]
        resumable = [
            self._deserialize_job(raw)
            for raw in phase_jobs
            if raw.get("status") in {"pending", "failed", "in_progress"}
        ]
        if resumable:
            for resumable_job in resumable:
                await queue.put(resumable_job)
                await self._set_job_status(resumable_job, "pending")
        elif not phase_jobs:
            root_job = DiscoveryJob(0, next(self._sequence), skip_auth=skip_auth)
            await queue.put(root_job)
            await self._set_job_status(root_job, "pending")

        async def worker(worker_number: int) -> None:
            try:
                context = self._client_factory()
                async with context as client:
                    crawler = Crawler(
                        client,
                        self._budget,
                        self._keywords,
                        login_url=self._login_url,
                        authenticate=self._authenticate,
                    )
                    crawler._base_url = base_url
                    authenticated_here = False
                    while True:
                        job = await queue.get()
                        fingerprint: str | None = None
                        try:
                            await self._set_job_status(job, "in_progress")
                            async with self._lock:
                                over_pages = len(self._seen) >= self._budget.max_pages
                                over_time = time.monotonic() - self._started >= self._budget.max_duration_seconds
                                if over_pages:
                                    self._safety_limit_kind = "states"
                                    self._termination = (
                                        "SAFETY_LIMIT_REACHED"
                                        if self._budget.automatic_limits else "MAX_PAGES_REACHED"
                                    )
                                elif over_time:
                                    self._safety_limit_kind = "duration"
                                    self._termination = (
                                        "SAFETY_LIMIT_REACHED"
                                        if self._budget.automatic_limits else "MAX_DURATION_REACHED"
                                    )
                                if over_pages or over_time:
                                    self._pending_at_limit += 1
                                    await self._set_job_status(job, "pending")
                                    continue
                                self._actions_examined += bool(job.path)

                            if not skip_auth and authenticated_here:
                                # This worker already owns an authenticated,
                                # isolated browser context. Reuse its persisted
                                # cookies/session rather than logging in again.
                                await client.navigate_page(base_url)
                                await client.wait_until_ready()
                                snapshot = await crawler._apply_path(
                                    await client.take_snapshot(), job.path
                                )
                            else:
                                snapshot = await crawler._replay_to(job.path, skip_auth=skip_auth)
                            if not skip_auth and not authenticated_here:
                                authenticated_here = True
                                async with self._lock:
                                    self._authenticated_workers += 1
                            captured: list[dict[str, Any]] = []

                            async def capture(state: dict[str, Any]) -> None:
                                captured.append(state)

                            recorded_path = job.path if skip_auth else [
                                ClickStep(role="authentication", name="Log in"), *job.path
                            ]
                            # Deduplication belongs to this pool, not to an
                            # individual browser worker. Clear the legacy
                            # crawler-local cache so alternate-parent edges are
                            # still observed and merged into app_flow_graph.
                            crawler._visited_fingerprints.clear()
                            fingerprint, nodes = await crawler._record_state(snapshot, recorded_path, capture)
                            if not fingerprint:
                                await self._set_job_status(job, "completed")
                                continue
                            state = captured[0] if captured else None
                            area_id = self._catalog_state(
                                fingerprint,
                                state or {"url_pattern": base_url},
                                nodes,
                                not skip_auth,
                                job.module_id,
                            )
                            action = self._action_text(job.path[-1]) if job.path else "ROOT"
                            async with self._lock:
                                if job.parent_fingerprint:
                                    self._edges.add((job.parent_fingerprint, fingerprint, action))
                                is_new = fingerprint not in self._seen
                                if is_new and len(self._seen) < self._budget.max_pages:
                                    self._seen.add(fingerprint)
                                    if state:
                                        self._nodes[fingerprint] = {
                                            "fingerprint": fingerprint,
                                            "url_pattern": state["url_pattern"],
                                            "area_id": area_id,
                                            "module_id": job.module_id,
                                        }
                                    if area_id not in self._area_roots:
                                        self._area_roots[area_id] = fingerprint
                                    if area_id == "dashboard" and "login" in self._area_roots:
                                        self._edges.add(
                                            (
                                                self._area_roots["login"],
                                                fingerprint,
                                                "authenticate(completion=dashboard)",
                                            )
                                        )
                                should_expand = (
                                    fingerprint not in self._expanded
                                    and fingerprint not in self._expanding
                                )
                                if should_expand:
                                    self._expanding.add(fingerprint)
                            if is_new and state:
                                # Existing repository/session is intentionally serialized.
                                async with self._persist_lock:
                                    await on_state_discovered(state)
                            if should_expand:
                                expansion_complete = await self._enqueue_children(
                                    queue,
                                    nodes,
                                    job.path,
                                    skip_auth,
                                    fingerprint,
                                    base_url,
                                    area_id,
                                    job.module_id,
                                )
                                async with self._lock:
                                    self._expanding.discard(fingerprint)
                                    if expansion_complete:
                                        self._expanded.add(fingerprint)
                                await self._save_checkpoint()
                            await self._set_job_status(job, "completed")
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:  # one page must not stop the pool
                            if fingerprint:
                                async with self._lock:
                                    self._expanding.discard(fingerprint)
                            screenshot_ref = None
                            with suppress(Exception):
                                screenshot_ref = str(await client.take_screenshot())
                            self._failures.append({
                                "worker": str(worker_number),
                                "action": self._action_text(job.path[-1]) if job.path else "ROOT",
                                "error": type(exc).__name__,
                                "detail": re.sub(
                                    r"(?i)(password|token|secret|api.?key)=[^\s&]+",
                                    r"\1=<redacted>",
                                    str(exc),
                                )[:300],
                                "screenshot_ref": screenshot_ref,
                            })
                            await self._set_job_status(job, "failed")
                        finally:
                            queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # An MCP process may fail before it can consume a job. Other
                # pool members continue; record the startup failure explicitly.
                self._failures.append({
                    "worker": str(worker_number),
                    "action": "start isolated Chrome DevTools MCP worker",
                    "error": type(exc).__name__,
                    "detail": str(exc)[:300],
                })

        workers = [asyncio.create_task(worker(i + 1)) for i in range(self._worker_limit)]
        join_task = asyncio.create_task(queue.join())
        try:
            while not join_task.done():
                await asyncio.wait([join_task, *workers], return_when=asyncio.FIRST_COMPLETED)
                if all(task.done() for task in workers) and not join_task.done():
                    self._termination = "ACTION_FAILURES"
                    while True:
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        else:
                            queue.task_done()
                    await join_task
        finally:
            if not join_task.done():
                join_task.cancel()
            for task in workers:
                task.cancel()
            for task in workers:
                with suppress(asyncio.CancelledError):
                    await task

    async def crawl(self, base_url: str, on_state_discovered: OnStateDiscovered) -> str:
        self._started = time.monotonic()
        phases = [True, False] if self._authenticate else [True]
        for skip_auth in phases:
            if self._termination != "EXPLORATION_EXHAUSTED":
                break
            await self._run_phase(base_url, skip_auth, on_state_discovered)
        if self._termination == "EXPLORATION_EXHAUSTED":
            if self._failures:
                self._termination = "ACTION_FAILURES"
            elif self._depth_limited:
                self._termination = (
                    "SAFETY_LIMIT_REACHED"
                    if self._budget.automatic_limits else "MAX_DEPTH_REACHED"
                )
        self.termination_reason = self._termination
        self.coverage = {
            "states_discovered": len(self._seen),
            "actions_examined": self._actions_examined,
            "actions_remaining": self._pending_at_limit + self._depth_limited,
            "queue_exhausted": self._pending_at_limit == 0 and self._depth_limited == 0,
            "failed_actions": self._failures,
            "skipped_actions": sorted(self._skipped),
            "authenticated_explored": self._authenticated_workers > 0,
            "worker_limit": self._worker_limit,
            "automatic_limits": self._budget.automatic_limits,
            "safety_limit_kind": self._safety_limit_kind,
            "app_flow_graph": {
                "nodes": list(self._nodes.values()),
                "edges": [
                    {"parent_fingerprint": parent, "child_fingerprint": child, "action": action}
                    for parent, child, action in sorted(self._edges)
                ],
                "subgraphs": list(self._areas.values()),
            },
            "discovery_catalog": {
                "mode": self._discovery_mode,
                "areas": list(self._areas.values()),
                "modules": list(self._modules.values()),
                "selected_areas": sorted(self._selected_areas),
                "selected_modules": sorted(self._selected_modules),
                "completion_condition": (
                    "selected_scope_exhausted"
                    if self._discovery_mode != "inventory"
                    else "top_level_areas_identified"
                ),
            },
            "scope": "Observed navigation and permitted controls; form submissions are not covered",
        }
        if not self._seen:
            recoverable = any(
                job.get("status") in {"pending", "failed", "in_progress"}
                for job in self._job_states.values()
            )
            return "PARTIAL" if recoverable else "FAILED"
        return "COMPLETE" if self._termination == "EXPLORATION_EXHAUSTED" else "PARTIAL"
