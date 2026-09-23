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


@dataclass(order=True)
class DiscoveryJob:
    priority: float
    sequence: int
    path: list[ClickStep] = field(compare=False, default_factory=list)
    skip_auth: bool = field(compare=False, default=False)
    parent_fingerprint: str | None = field(compare=False, default=None)


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
    ) -> None:
        self._client_factory = client_factory
        self._budget = budget
        self._keywords = keywords
        self._login_url = login_url
        self._authenticate = authenticate
        self._worker_limit = max(1, min(worker_limit, 5))
        self._seen: set[str] = set()
        self._expanded: set[str] = set()
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
    ) -> None:
        if len(path) >= self._budget.max_depth:
            self._depth_limited += 1
            if self._budget.automatic_limits:
                self._safety_limit_kind = "depth"
            return
        root = next((node for node in nodes if node.get("role") == "RootWebArea"), {})
        current_url = root.get("url") or base_url
        for element in nodes:
            role, name = element.get("role", ""), element.get("name", "")
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
                await queue.put(DiscoveryJob(
                    priority=-_relevance_score(name, self._keywords),
                    sequence=next(self._sequence),
                    path=[*path, step],
                    skip_auth=skip_auth,
                    parent_fingerprint=parent_fingerprint,
                ))

    async def _run_phase(
        self,
        base_url: str,
        skip_auth: bool,
        on_state_discovered: OnStateDiscovered,
    ) -> None:
        queue: asyncio.PriorityQueue[DiscoveryJob] = asyncio.PriorityQueue()
        await queue.put(DiscoveryJob(0, next(self._sequence), skip_auth=skip_auth))

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
                        try:
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
                                continue
                            state = captured[0] if captured else None
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
                                        }
                                should_expand = fingerprint not in self._expanded
                                if should_expand:
                                    self._expanded.add(fingerprint)
                            if is_new and state:
                                # Existing repository/session is intentionally serialized.
                                async with self._persist_lock:
                                    await on_state_discovered(state)
                            if should_expand:
                                await self._enqueue_children(
                                    queue, nodes, job.path, skip_auth, fingerprint, base_url
                                )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:  # one page must not stop the pool
                            self._failures.append({
                                "worker": str(worker_number),
                                "action": self._action_text(job.path[-1]) if job.path else "ROOT",
                                "error": type(exc).__name__,
                                "detail": re.sub(
                                    r"(?i)(password|token|secret|api.?key)=[^\s&]+",
                                    r"\1=<redacted>",
                                    str(exc),
                                )[:300],
                            })
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
            },
            "scope": "Observed navigation and permitted controls; form submissions are not covered",
        }
        if not self._seen:
            return "FAILED"
        return "COMPLETE" if self._termination == "EXPLORATION_EXHAUSTED" else "PARTIAL"
