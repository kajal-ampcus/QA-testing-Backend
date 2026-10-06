"""Bounded parallel discovery built on the existing deterministic Crawler.

Each pool worker owns one isolated Chrome DevTools MCP process. Workers share
only crawl coordination state (frontier, deduplication and flow graph); browser
pages and authentication cookies never leak between worker contexts.
"""

import asyncio
import hashlib
import itertools
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from core.agents.application_discovery.crawler import (
    ClickStep,
    CrawlBudget,
    Crawler,
    _is_chrome_navigation_control,
    _is_external,
    _is_transient_widget_control,
    _relevance_score,
)
from core.agents.application_discovery.fingerprint import (
    auth_flow_label,
    functional_page_key,
    collection_count_badge,
    gated_probe_kind,
    unlabeled_collection_icons,
    is_chrome_label,
    is_collection_mutation_action,
    is_gated_reveal_action,
    is_in_page_content_control,
    observed_page_label,
    repeated_in_page_control,
)
from core.policy_safety.destructive_action_lexicon import classify_risk
from core.tool_gateway.gateway import BrowserInspection, SecurityVerificationRequiredError
from domain.enums import RiskLevel

_CHECKPOINT_INTERVAL_SECONDS = 2.0


ClientFactory = Callable[[], AbstractAsyncContextManager[BrowserInspection]]
OnStateDiscovered = Callable[[dict[str, Any]], Awaitable[None]]
OnCheckpoint = Callable[[dict[str, Any]], Awaitable[None]]


def _is_login_form_chrome(nodes: list[dict[str, Any]], role: str, destination: str | None) -> bool:
    """Skip controls that only change the current login form.

    Role buttons such as Employee, Kitchen, and Admin, plus show-password
    and refresh-CAPTCHA, have no destination. Exploring them never leaves
    the login page and used to block each click for the full readiness timeout.
    Links such as Forgot Password still navigate and stay eligible.
    """
    if destination or role not in {"button", "radio", "checkbox"}:
        return False
    return any(
        node.get("role") in {"textbox", "input"}
        and re.search(r"\bpassword\b", str(node.get("name", "")), re.I)
        for node in nodes
    )


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
        selected_auth_flow: str | None = None,
        selected_auth_flows: list[str] | None = None,
        auth_entry_url: str | None = None,
        auth_entry_urls: list[str] | None = None,
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
        self._selected_auth_flow = selected_auth_flow
        self._selected_auth_flows = list(
            dict.fromkeys(
                selected_auth_flows
                or ([selected_auth_flow] if selected_auth_flow else [])
            )
        )
        self._auth_entry_url = auth_entry_url
        self._auth_entry_urls = list(
            dict.fromkeys(auth_entry_urls or ([auth_entry_url] if auth_entry_url else []))
        )
        self._selected_areas = {value.lower() for value in (selected_areas or [])}
        self._selected_modules = {value.lower() for value in (selected_modules or [])}
        self._areas: dict[str, dict[str, Any]] = {}
        self._modules: dict[str, dict[str, Any]] = {}
        self._auth_flows: dict[str, dict[str, Any]] = {}
        self._module_inventory_flows: set[str] = set()
        self._area_roots: dict[str, str] = {}
        self._checkpoint = checkpoint or {}
        self._on_checkpoint = on_checkpoint
        self._job_states: dict[str, dict[str, Any]] = {}
        self._seen: set[str] = set()
        self._page_keys: dict[str, str] = {}
        self._live_view: dict[str, Any] | None = None
        self._expanded: set[str] = set()
        self._expanding: set[str] = set()
        self._queued_actions: set[tuple[str, str, str, str]] = set()
        self._failures: list[dict[str, str]] = []
        self._skipped: set[str] = set()
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: set[tuple[str, str, str]] = set()
        self._lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._last_checkpoint_at = float("-inf")
        self._sequence = itertools.count()
        self._started = 0.0
        self._actions_examined = 0
        self._depth_limited = 0
        self._pending_at_limit = 0
        self._termination = "EXPLORATION_EXHAUSTED"
        self._authenticated_workers = 0
        self._session_state: dict[str, Any] | None = None
        self._landing_url: str | None = None
        self._active_workers = 0
        self._auth_status = "pending" if authenticate else "not_required"
        self._dashboard_status = "pending"
        self._safety_limit_kind: str | None = None
        self.termination_reason = "EXPLORATION_EXHAUSTED"
        self.coverage: dict[str, Any] = {}
        self._reveal_probed = False
        self._collection_follow_queued = False
        self._restore_checkpoint()

    @staticmethod
    def _serialize_step(step: ClickStep) -> dict[str, Any]:
        return {
            "role": step.role,
            "name": step.name,
            "url": step.url,
            "value": step.value,
            "occurrence": step.occurrence,
        }

    @classmethod
    def _job_key(cls, job: DiscoveryJob) -> str:
        phase = "public" if job.skip_auth else "authenticated"
        path = "/".join(
            f"{step.role}:{step.name}:{step.url or ''}:{step.value or ''}:{step.occurrence}"
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
        self._page_keys = {
            node["functional_key"]: node["fingerprint"]
            for node in self._nodes.values()
            if node.get("functional_key")
        }
        self._live_view = self._checkpoint.get("live_view")
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
        self._auth_flows = {
            flow["id"]: flow
            for flow in self._checkpoint.get("authentication_flows", [])
        }
        self._module_inventory_flows = set(
            self._checkpoint.get("module_inventory_flows", [])
        )
        self._job_states = {
            job["key"]: job for job in self._checkpoint.get("jobs", [])
        }
        self._reveal_probed = bool(self._checkpoint.get("reveal_probed"))
        self._collection_follow_queued = bool(self._checkpoint.get("collection_follow_queued"))

    def _checkpoint_payload(self) -> dict[str, Any]:
        jobs = list(self._job_states.values())
        return {
            "version": 1,
            "progress": self._progress(),
            "live_view": self._live_view,
            "configuration": {
                "mode": self._discovery_mode,
                "selected_auth_flow": self._selected_auth_flow,
                "selected_auth_flows": self._selected_auth_flows,
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
            "authentication_flows": list(self._auth_flows.values()),
            "module_inventory_flows": sorted(self._module_inventory_flows),
            "reveal_probed": self._reveal_probed,
            "collection_follow_queued": self._collection_follow_queued,
        }

    async def _save_checkpoint(self, *, force: bool = False) -> None:
        """Persist progress. Every job status change calls this, so writes
        are throttled; phase boundaries and terminations pass force=True."""
        if not self._on_checkpoint:
            return
        if not force and time.monotonic() - self._last_checkpoint_at < _CHECKPOINT_INTERVAL_SECONDS:
            return
        # State inserts and checkpoint updates share one SQLAlchemy
        # session in the worker; serialize both transaction types.
        async with self._persist_lock:
            self._last_checkpoint_at = time.monotonic()
            await self._on_checkpoint(self._checkpoint_payload())

    def _progress(self) -> dict[str, Any]:
        statuses = [job["status"] for job in self._job_states.values()]
        return {
            "authentication": self._auth_status,
            "dashboard_discovery": self._dashboard_status,
            "task_queue": len(statuses),
            "active_workers": self._active_workers,
            "discovered_states": len(self._nodes),
            "discovered_transitions": len(self._edges),
            "pending_tasks": statuses.count("pending"),
            "failed_tasks": statuses.count("failed"),
            "skipped_unsafe_actions": len(self._skipped),
        }

    async def _set_job_status(self, job: DiscoveryJob, status: str) -> None:
        self._job_states[self._job_key(job)] = self._serialize_job(job, status)
        await self._save_checkpoint()

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "area"

    @staticmethod
    def _observed_label(state: dict[str, Any], nodes: list[dict[str, Any]]) -> str:
        return observed_page_label(nodes, str(state.get("url_pattern") or ""), "Public entry")

    def _detect_auth_flow(
        self, fingerprint: str, state: dict[str, Any], nodes: list[dict[str, Any]], authenticated: bool
    ) -> dict[str, Any] | None:
        if authenticated:
            return None
        url_pattern = state.get("url_pattern", "/")
        primary_identity = " ".join(
            [url_pattern, self._observed_label(state, nodes)]
            + [
                str(node.get("name", ""))
                for node in nodes
                if node.get("role") in {"heading", "dialog", "textbox", "button"}
            ]
        )
        has_password = any(
            node.get("input_type") == "password"
            or (node.get("role") == "textbox" and re.search(r"password|passcode|pin", str(node.get("name", "")), re.I))
            for node in nodes
        )
        has_identifier = any(
            node.get("role") in {"textbox", "combobox"}
            for node in nodes
        )
        page_identity = " ".join(
            [url_pattern, self._observed_label(state, nodes)]
            + [
                str(node.get("name", ""))
                for node in nodes
                if node.get("role") in {"heading", "dialog"}
            ]
        )
        page_auth_words = re.search(
            r"\b(log\s*in|sign\s*in|register|sign\s*up|create\s+account|forgot|reset|recover|password|passcode)\b",
            page_identity,
            re.I,
        )
        auth_submit = any(
            node.get("role") == "button"
            and re.search(
                r"\b(log\s*in|sign\s*in|register|sign\s*up|create\s+account|reset|recover|continue)\b",
                str(node.get("name", "")),
                re.I,
            )
            for node in nodes
        )
        if not has_password and not (has_identifier and (page_auth_words or auth_submit)):
            return None
        if re.search(r"\b(register|sign\s*up|create\s+account|confirm\s+password)\b", primary_identity, re.I):
            kind = "registration"
        elif re.search(r"\b(forgot|reset|recover)\b", primary_identity, re.I):
            kind = "recovery"
        elif has_password or re.search(r"\b(log\s*in|sign\s*in)\b", primary_identity, re.I):
            kind = "login"
        else:
            kind = "authentication"
        return {
            "id": f"auth-{fingerprint[:12]}",
            "label": auth_flow_label(kind),
            "kind": kind,
            "url_pattern": url_pattern,
            "state_fingerprint": fingerprint,
            "selectable": True,
        }

    def _classify_state(
        self, state: dict[str, Any], nodes: list[dict[str, Any]], authenticated: bool
    ) -> tuple[str, str]:
        if authenticated:
            return "authenticated", self._observed_label(state, nodes)
        flow = self._detect_auth_flow("classification", state, nodes, False)
        if flow:
            return f"authentication-{flow['kind']}", flow["label"]
        return "public", self._observed_label(state, nodes)

    def _authenticate_source_flows(self) -> list[dict[str, Any]]:
        selected_ids = list(
            dict.fromkeys(
                [
                    *self._selected_auth_flows,
                    *([self._selected_auth_flow] if self._selected_auth_flow else []),
                ]
            )
        )
        selected = [self._auth_flows[flow_id] for flow_id in selected_ids if flow_id in self._auth_flows]
        if selected:
            return selected
        login_flows = [flow for flow in self._auth_flows.values() if flow.get("kind") == "login"]
        return login_flows or list(self._auth_flows.values())

    def _catalog_state(
        self,
        fingerprint: str,
        state: dict[str, Any],
        nodes: list[dict[str, Any]],
        authenticated: bool,
        module_id: str | None = None,
    ) -> str:
        flow = self._detect_auth_flow(fingerprint, state, nodes, authenticated)
        if flow:
            self._auth_flows.setdefault(flow["id"], flow)
            if (
                self._discovery_mode in {"targeted", "full", "complete"}
                and flow["kind"] == "login"
                and not self._selected_auth_flow
            ):
                self._selected_auth_flow = flow["id"]
        area_id, label = self._classify_state(state, nodes, authenticated)
        area = self._areas.setdefault(
            area_id,
            {
                "id": area_id,
                "label": label,
                "kind": (
                    "authenticated"
                    if area_id == "authenticated"
                    else (
                        "authentication"
                        if area_id.startswith("authentication-")
                        else "public"
                    )
                ),
                "state_fingerprints": [],
                "selectable": True,
            },
        )
        if fingerprint not in area["state_fingerprints"]:
            area["state_fingerprints"].append(fingerprint)
        if (
            (area_id == "authenticated" or (not self._authenticate and self._discovery_mode in {"targeted", "full"}))
            and (self._selected_auth_flow or "default") not in self._module_inventory_flows
        ):
            for node in nodes:
                if node.get("role") not in {"link", "button", "menuitem", "tab"}:
                    continue
                name = str(node.get("name", "")).strip()
                if not name or is_chrome_label(name):
                    continue
                if not self._safe_navigation(node):
                    self._skipped.add(f"{state.get('url_pattern', '/')}: {node.get('role')} {name}")
                    continue
                if classify_risk(str(node.get("role", "")), name) == RiskLevel.DESTRUCTIVE:
                    continue
                destination = str(node.get("url") or "")
                root_url = next((item.get("url") for item in nodes if item.get("role") == "RootWebArea"), "") or ""
                if destination and (urlparse(urljoin(root_url, destination)).scheme not in {"http", "https"} or _is_external(urljoin(root_url, destination), root_url)):
                    continue
                action_key = f"{node.get('role', '')}|{name}|{destination}"
                candidate_id = self._slug(name)
                existing = self._modules.get(candidate_id)
                if existing and (
                    existing.get("action_key") != action_key
                    or existing.get("auth_flow_id") != self._selected_auth_flow
                ):
                    suffix = hashlib.sha1(action_key.encode("utf-8")).hexdigest()[:8]
                    candidate_id = f"{candidate_id}-{suffix}"
                module = self._modules.setdefault(
                    candidate_id,
                    {
                        "id": candidate_id,
                        "label": name,
                        "area_id": area_id,
                        "auth_flow_id": self._selected_auth_flow,
                        "action_key": action_key,
                        "entry_action": {
                            "role": node.get("role"),
                            "name": name,
                            "url": node.get("url"),
                        },
                        "state_fingerprints": [],
                    },
                )
                if fingerprint not in module["state_fingerprints"]:
                    module["state_fingerprints"].append(fingerprint)
            self._module_inventory_flows.add(self._selected_auth_flow or "default")
        if module_id and module_id in self._modules:
            module_fingerprints = self._modules[module_id]["state_fingerprints"]
            if fingerprint not in module_fingerprints:
                module_fingerprints.append(fingerprint)
        return area_id

    def _selected_module_for_element(self, element: dict[str, Any]) -> str | None:
        return next(
            (
                candidate
                for candidate, module in self._modules.items()
                if candidate in self._selected_modules
                and module.get("entry_action", {}).get("role") == element.get("role")
                and module.get("entry_action", {}).get("name") == element.get("name")
                and (
                    not module.get("entry_action", {}).get("url")
                    or module.get("entry_action", {}).get("url") == element.get("url")
                )
            ),
            None,
        )

    def _action_in_scope(
        self, area_id: str, element: dict[str, Any], depth: int, module_id: str | None
    ) -> bool:
        if self._discovery_mode in {"complete", "full"}:
            return True
        if self._discovery_mode in {"entry_points", "inventory"}:
            return not area_id.startswith("authenticated") and depth <= 1
        if self._discovery_mode == "auth_flow":
            return not area_id.startswith("authenticated")
        if self._discovery_mode == "modules":
            return False
        if area_id != "authenticated" and not (self._discovery_mode == "targeted" and not self._authenticate):
            return False
        if self._discovery_mode == "targeted":
            # Global navigation is often repeated inside a selected branch.
            # Do not cross into another observed root branch through its menu.
            candidate = next((key for key, item in self._modules.items()
                if item.get("entry_action", {}).get("name") == element.get("name")
                and item.get("entry_action", {}).get("role") == element.get("role")
                and item.get("entry_action", {}).get("url") == element.get("url")), None)
            if candidate and candidate not in self._selected_modules:
                return False
        if module_id in self._selected_modules:
            return True
        return self._selected_module_for_element(element) is not None

    @staticmethod
    def _safe_navigation(element: dict[str, Any]) -> bool:
        role = str(element.get("role", ""))
        name = str(element.get("name", ""))
        destination = str(element.get("url") or "")
        if element.get("disabled") or element.get("visible") is False:
            return False
        if classify_risk(role, name) != RiskLevel.SAFE:
            return False
        if re.search(r"\b(log.?out|sign.?out|delete|remove|purchase|checkout|payment|transfer|withdraw|revoke|erase|terminate|place order|order now|book now|refund|charge|execute)\b", name + " " + destination, re.I):
            return False
        if element.get("input_type") == "submit":
            return False
        if role == "link" and destination:
            return True
        # Choosing a radio or an observed combobox option reveals dependent
        # UI without submitting anything; checkboxes are excluded because
        # they usually toggle a persisted setting.
        if role == "radio":
            return True
        if role == "combobox":
            return bool(element.get("options"))
        return role in {"button", "menuitem", "tab"} and not re.search(
            r"\b(submit|send|save|subscribe|confirm|approve|publish|register|create account|log in|sign in|login)\b", name, re.I
        )

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
        allow_collection_follow: bool = False,
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
        icons = unlabeled_collection_icons(nodes)
        for element in nodes:
            role, name = element.get("role", ""), element.get("name", "")
            if _is_transient_widget_control(role, name):
                continue
            if _is_chrome_navigation_control(role, name, element.get("url")):
                self._skipped.add(f"{urlparse(current_url).path}: {role} {name}")
                continue
            if _is_login_form_chrome(nodes, role, element.get("url")):
                self._skipped.add(f"{urlparse(current_url).path}: {role} {name}")
                continue
            if not self._action_in_scope(area_id, element, len(path), module_id):
                continue
            if role not in {"link", "button", "menuitem", "tab", "radio", "combobox"}:
                continue
            if element.get("disabled") or element.get("visible") is False:
                continue
            destination = element.get("url")
            if destination:
                destination = urljoin(current_url, destination)
                if urlparse(destination).scheme not in {"http", "https"} or _is_external(destination, base_url):
                    continue
                if urldefrag(destination)[0] == urldefrag(current_url)[0] and "#" in destination and not urlparse(destination).fragment.startswith(("/", "!/")):
                    continue
            probe_kind = gated_probe_kind(
                name, element.get("description"), destination or element.get("url"), current_url
            )
            if (
                probe_kind is None
                and allow_collection_follow
                and collection_count_badge(name, nodes)
            ):
                probe_kind = "collection"
            if probe_kind is None and element in icons:
                probe_kind = "collection"
            occurrence = icons.index(element) if element in icons else 0
            safe_link = role == "link" and bool(destination)
            form_action = bool(re.search(
                r"\b(submit|send|subscribe|save|sign out|log out|logout|sign in|log in|login|register|create account)\b",
                name,
                re.I,
            ))
            blocked_mutation = bool(
                re.search(
                    r"\b(log.?out|sign.?out|delete|purchase|checkout|payment|transfer|withdraw)\b",
                    " ".join(filter(None, [name, str(destination or ""), str(element.get("description") or "")])),
                    re.I,
                )
            )
            icon_collection = probe_kind == "collection" and not str(name or "").strip()
            if icon_collection and (
                classify_risk(role, name or "cart") == RiskLevel.DESTRUCTIVE
                or is_collection_mutation_action(name)
                or blocked_mutation
                or element.get("input_type") == "submit"
            ):
                self._skipped.add(f"{urlparse(current_url).path}: {role} {name}")
                continue
            if not icon_collection and (
                not self._safe_navigation(element)
                or is_collection_mutation_action(name)
                or blocked_mutation
                or (form_action and not safe_link)
                or element.get("input_type") == "submit"
            ):
                self._skipped.add(f"{urlparse(current_url).path}: {role} {name}")
                continue
            in_page = is_in_page_content_control(role, name, element.get("url"), current_url)
            repeated = not destination and repeated_in_page_control(name, nodes)
            if probe_kind not in {"reveal", "collection"} and (in_page or repeated):
                self._skipped.add(f"{urlparse(current_url).path}: {role} {name}")
                continue
            values = element.get("options", []) if role == "combobox" else [None]
            for value in values:
                canonical = destination.rstrip("/") if destination else None
                identity = canonical or name or str(element.get("description") or "")
                key = (parent_fingerprint, role, identity, str(value), occurrence)
                async with self._lock:
                    if key in self._queued_actions:
                        continue
                    if probe_kind == "reveal":
                        if self._reveal_probed:
                            continue
                        self._reveal_probed = True
                    elif probe_kind == "collection":
                        unlabeled = not str(name or "").strip()
                        if unlabeled:
                            blocked = occurrence >= 2
                        else:
                            blocked = (
                                not allow_collection_follow
                                or not self._reveal_probed
                                or self._collection_follow_queued
                            )
                        if blocked:
                            continue
                        if not unlabeled or str(element.get("description") or "").strip():
                            self._collection_follow_queued = True
                    elif in_page or repeated:
                        continue
                    self._queued_actions.add(key)
                step = ClickStep(
                    role,
                    name or str(element.get("description") or ""),
                    destination,
                    value,
                    occurrence,
                )
                child_module = module_id
                if child_module is None:
                    child_module = next((key for key, item in self._modules.items()
                        if item.get("entry_action", {}).get("role") == role
                        and item.get("entry_action", {}).get("name") == name
                        and item.get("entry_action", {}).get("url") == element.get("url")), None)
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
                            self._active_workers += 1
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

                            if skip_auth:
                                await client.navigate_page(base_url)
                                await client.wait_until_ready()
                                snapshot = await crawler._apply_path(
                                    await client.take_snapshot(), job.path
                                )
                            elif self._session_state is not None:
                                if not authenticated_here:
                                    await client.import_authenticated_session(self._session_state)
                                await client.navigate_page(self._landing_url or base_url)
                                await client.wait_until_ready()
                                snapshot = await crawler._apply_path(await client.take_snapshot(), job.path)
                            elif authenticated_here:
                                # This worker already owns an authenticated,
                                # isolated browser context. Reuse its persisted
                                # cookies/session rather than logging in again.
                                await client.navigate_page(base_url)
                                await client.wait_until_ready()
                                snapshot = await crawler._apply_path(
                                    await client.take_snapshot(), job.path
                                )
                            else:
                                async with self._lock:
                                    self._live_view = {
                                        "url": self._login_url or base_url,
                                        "label": "Signing in",
                                        "screenshot_ref": None,
                                        "fingerprint": None,
                                        "action": "authenticate",
                                    }
                                await self._save_checkpoint()
                                snapshot = await crawler._replay_to(job.path, skip_auth=skip_auth)
                            if not skip_auth and not authenticated_here:
                                authenticated_here = True
                                async with self._lock:
                                    self._authenticated_workers += 1
                            captured: list[dict[str, Any]] = []

                            async def capture(
                                state: dict[str, Any], sink: list[dict[str, Any]] = captured
                            ) -> None:
                                sink.append(state)

                            recorded_path = job.path if skip_auth else [
                                ClickStep(role="authentication", name="Log in"), *job.path
                            ]
                            # Deduplication belongs to this pool, not to an
                            # individual browser worker. Clear the legacy
                            # crawler-local cache so alternate-parent edges are
                            # still observed and merged into app_flow_graph.
                            crawler._visited_fingerprints.clear()
                            fingerprint, nodes = await crawler._record_state(snapshot, recorded_path, capture)
                            root = next((node for node in nodes if node.get("role") == "RootWebArea"), {})
                            live_url = str(root.get("url") or (captured[0]["url_pattern"] if captured else base_url))
                            page_key = functional_page_key(live_url, nodes) if nodes else ""
                            action = self._action_text(job.path[-1]) if job.path else "ROOT"
                            screenshot_ref = captured[0].get("evidence_ref") if captured else None
                            if not screenshot_ref:
                                with suppress(Exception):
                                    screenshot_ref = str(await client.take_screenshot())
                            if not fingerprint:
                                async with self._lock:
                                    canonical = self._page_keys.get(page_key)
                                    self._live_view = {
                                        "url": live_url,
                                        "label": observed_page_label(nodes, live_url),
                                        "screenshot_ref": screenshot_ref,
                                        "fingerprint": canonical,
                                        "action": action,
                                    }
                                await self._set_job_status(job, "completed")
                                continue
                            state = captured[0] if captured else None
                            async with self._lock:
                                canonical = self._page_keys.get(page_key)
                                if canonical:
                                    fingerprint = canonical
                                if fingerprint not in self._seen and len(self._seen) >= self._budget.max_pages:
                                    self._pending_at_limit += 1
                                    self._termination = "MAX_PAGES_REACHED"
                                    await self._set_job_status(job, "pending")
                                    continue
                                area_id = self._catalog_state(
                                    fingerprint, state or {"url_pattern": base_url},
                                    nodes, not skip_auth, job.module_id,
                                )
                                if job.parent_fingerprint:
                                    self._edges.add((job.parent_fingerprint, fingerprint, action))
                                is_new = fingerprint not in self._seen and canonical is None
                                if is_new and len(self._seen) < self._budget.max_pages:
                                    self._seen.add(fingerprint)
                                    if page_key:
                                        self._page_keys[page_key] = fingerprint
                                    if state:
                                        self._nodes[fingerprint] = {
                                            "fingerprint": fingerprint,
                                            "url_pattern": state["url_pattern"],
                                            "functional_key": page_key,
                                            "label": observed_page_label(nodes, live_url),
                                            "area_id": area_id,
                                            "module_id": job.module_id,
                                        }
                                    if area_id not in self._area_roots:
                                        self._area_roots[area_id] = fingerprint
                                selected_flows = self._authenticate_source_flows()
                                if area_id == "authenticated" and not job.path:
                                    self._edges.update(
                                        (
                                            selected_flow["state_fingerprint"],
                                            fingerprint,
                                            f"authenticate(flow={selected_flow['id']})",
                                        )
                                        for selected_flow in selected_flows
                                        if selected_flow.get("state_fingerprint")
                                        and selected_flow["state_fingerprint"] != fingerprint
                                    )
                                reveal_replay = bool(
                                    job.path and is_gated_reveal_action(job.path[-1].name)
                                )
                                should_expand = fingerprint not in self._expanding and (
                                    fingerprint not in self._expanded or reveal_replay
                                )
                                if should_expand:
                                    self._expanding.add(fingerprint)
                                self._live_view = {
                                    "url": live_url,
                                    "label": observed_page_label(nodes, live_url),
                                    "screenshot_ref": screenshot_ref or (state or {}).get("evidence_ref"),
                                    "fingerprint": fingerprint,
                                    "action": action,
                                }
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
                                    allow_collection_follow=reveal_replay,
                                )
                                async with self._lock:
                                    self._expanding.discard(fingerprint)
                                    if expansion_complete:
                                        self._expanded.add(fingerprint)
                                await self._save_checkpoint()
                            await self._set_job_status(job, "completed")
                        except asyncio.CancelledError:
                            raise
                        except SecurityVerificationRequiredError as exc:
                            screenshot_ref = None
                            with suppress(Exception):
                                screenshot_ref = str(await client.take_screenshot())
                            self._termination = "SECURITY_VERIFICATION_REQUIRED"
                            self._failures.append({
                                "worker": str(worker_number),
                                "action": self._action_text(job.path[-1]) if job.path else "ROOT",
                                "phase": "public" if skip_auth else "authenticated",
                                "error": type(exc).__name__,
                                "detail": str(exc)[:600],
                                "screenshot_ref": screenshot_ref,
                            })
                            # Keep the blocked frontier resumable. Once test/staging access
                            # is configured, Continue Discovery retries this exact job.
                            await self._set_job_status(job, "pending")
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
                                )[:2000],
                                "screenshot_ref": screenshot_ref,
                            })
                            await self._set_job_status(job, "failed")
                        finally:
                            self._active_workers -= 1
                            await self._save_checkpoint()
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
                    "detail": str(exc)[:2000],
                })

        workers = [asyncio.create_task(worker(i + 1)) for i in range(self._worker_limit)]
        join_task = asyncio.create_task(queue.join())
        try:
            while not join_task.done():
                await asyncio.wait([join_task, *(task for task in workers if not task.done())], return_when=asyncio.FIRST_COMPLETED)
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
        if self._discovery_mode in {"targeted", "full"}:
            try:
                await asyncio.wait_for(self._bootstrap(base_url, on_state_discovered), self._budget.max_duration_seconds)
            except Exception as exc:
                self._auth_status = "failed"
                self._failures.append({"action": "Authentication bootstrap", "error": type(exc).__name__, "detail": str(exc)[:300]})
                if self._termination == "EXPLORATION_EXHAUSTED":
                    self._termination = "AUTHENTICATION_FAILED"
                if isinstance(exc, TimeoutError):
                    self._termination = "MAX_DURATION_REACHED"
                await self._save_checkpoint(force=True)
            phases = [(self._landing_url or base_url, not self._authenticate)]
            if self._discovery_mode == "targeted" and not self._selected_modules:
                phases = []
        elif self._discovery_mode in {"entry_points", "inventory"}:
            phases = [(base_url, True)]
        elif self._discovery_mode == "auth_flow":
            entry_urls = self._auth_entry_urls or [self._auth_entry_url or base_url]
            phases = [(url, True) for url in entry_urls]
        elif self._discovery_mode in {"modules", "deep"}:
            phases = [(base_url, False)] if self._authenticate else []
        else:
            public_entry_urls = [base_url, *self._auth_entry_urls]
            phases = [(url, True) for url in dict.fromkeys(public_entry_urls)]
            if self._authenticate:
                phases.append((base_url, False))
        for phase_url, skip_auth in phases:
            if self._termination != "EXPLORATION_EXHAUSTED":
                break
            remaining = self._budget.max_duration_seconds - (time.monotonic() - self._started)
            try:
                await asyncio.wait_for(self._run_phase(phase_url, skip_auth, on_state_discovered), max(0.001, remaining))
            except TimeoutError:
                self._termination = "MAX_DURATION_REACHED"
            await self._save_checkpoint(force=True)
        if self._termination == "EXPLORATION_EXHAUSTED":
            if self._failures and not self._nodes:
                self._termination = "ACTION_FAILURES"
            elif self._depth_limited:
                self._termination = (
                    "SAFETY_LIMIT_REACHED"
                    if self._budget.automatic_limits else "MAX_DEPTH_REACHED"
                )
        await self._save_checkpoint(force=True)
        self.termination_reason = self._termination
        self.coverage = {
            "progress": self._progress(),
            "states_discovered": len(self._seen),
            "actions_examined": self._actions_examined,
            "actions_remaining": self._pending_at_limit + self._depth_limited,
            "queue_exhausted": not any(job["status"] in {"pending", "failed", "in_progress"} for job in self._job_states.values()),
            "failed_actions": self._failures,
            "skipped_actions": sorted(self._skipped),
            "authenticated_explored": self._authenticated_workers > 0,
            "worker_limit": self._worker_limit,
            "automatic_limits": self._budget.automatic_limits,
            "safety_limit_kind": self._safety_limit_kind,
            "live_view": self._live_view,
            "security_verification_required": (
                self._termination == "SECURITY_VERIFICATION_REQUIRED"
            ),
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
                "stage": self._discovery_mode,
                "authentication_flows": list(self._auth_flows.values()),
                "areas": list(self._areas.values()),
                "modules": list(self._modules.values()),
                "module_inventory_flows": sorted(self._module_inventory_flows),
                "selected_auth_flow": self._selected_auth_flow,
                "selected_auth_flows": self._selected_auth_flows,
                "selected_areas": sorted(self._selected_areas),
                "selected_modules": sorted(self._selected_modules),
                "completion_condition": (
                    "selected_scope_exhausted"
                    if self._discovery_mode not in {"inventory", "entry_points", "modules"}
                    else (
                        "authentication_entry_points_identified"
                        if self._discovery_mode in {"inventory", "entry_points"}
                        else "authenticated_modules_identified"
                    )
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

    async def _bootstrap(self, base_url: str, on_state_discovered: OnStateDiscovered) -> None:
        """Observe entry/login and the actual landing state before scheduling branches."""
        async with self._client_factory() as client:
            crawler = Crawler(client, self._budget, self._keywords, login_url=self._login_url, authenticate=self._authenticate)
            crawler._base_url = base_url
            parent: str | None = None
            history: list[str] = []

            async def observe(authenticated: bool, action: str) -> tuple[str | None, list[dict[str, Any]]]:
                nonlocal parent
                captured: list[dict[str, Any]] = []
                async def capture(state: dict[str, Any]) -> None:
                    captured.append(state)
                crawler._visited_fingerprints.clear()
                fingerprint, nodes = await crawler._record_state(await client.take_snapshot(), [], capture)
                if not fingerprint or not captured:
                    raise RuntimeError("Entry page has no observable state")
                state = captured[0]
                if parent:
                    history.append(action)
                state["reached_via"] = list(history)
                area = self._catalog_state(fingerprint, state, nodes, authenticated)
                root = next((node for node in nodes if node.get("role") == "RootWebArea"), {})
                live_url = str(root.get("url") or state["url_pattern"])
                page_key = functional_page_key(live_url, nodes)
                if page_key:
                    self._page_keys.setdefault(page_key, fingerprint)
                self._live_view = {
                    "url": live_url,
                    "label": observed_page_label(nodes, live_url),
                    "screenshot_ref": state.get("evidence_ref"),
                    "fingerprint": fingerprint,
                    "action": action,
                }
                if fingerprint not in self._seen:
                    if len(self._seen) >= self._budget.max_pages:
                        self._termination = "MAX_PAGES_REACHED"
                        raise RuntimeError("Discovery reached the configured page limit during bootstrap")
                    await on_state_discovered(state)
                    self._seen.add(fingerprint)
                    self._nodes[fingerprint] = {
                        "fingerprint": fingerprint,
                        "url_pattern": state["url_pattern"],
                        "functional_key": page_key,
                        "label": observed_page_label(nodes, live_url),
                        "area_id": area,
                    }
                if parent and parent != fingerprint:
                    self._edges.add((parent, fingerprint, action))
                parent = fingerprint
                await self._save_checkpoint()
                return fingerprint, nodes

            await client.navigate_page(base_url)
            await client.wait_until_ready()
            _, nodes = await observe(False, "entry")
            if self._authenticate:
                self._auth_status = "in_progress"
                current_flow = self._detect_auth_flow("current", {"url_pattern": base_url}, nodes, False)
                if not current_flow or current_flow["kind"] not in {"login", "authentication"}:
                    # Follow an observed authentication link or the account's explicit URL.
                    login = next((node for node in nodes if node.get("role") in {"link", "button"}
                        and re.search(r"\b(log\s*in|sign\s*in)\b", node.get("name", ""), re.I)), None)
                    destination = urljoin(base_url, login["url"]) if login and login.get("url") else self._login_url
                    if login and not login.get("url") and login.get("uid"):
                        await client.click(login["uid"])
                        await client.wait_until_ready()
                        await observe(False, "open observed authentication")
                    elif destination and destination != base_url:
                        await client.navigate_page(destination)
                        await client.wait_until_ready()
                        await observe(False, "navigate to observed authentication")
                await self._save_checkpoint()
                await client.authenticate()
                await client.wait_until_ready()
                self._session_state = await client.export_authenticated_session()
                await client.wait_until_ready()
                self._auth_status = "complete"
                self._authenticated_workers = 1
            self._dashboard_status = "in_progress"
            fingerprint, nodes = await observe(self._authenticate, "authenticate")
            root = next((node for node in nodes if node.get("role") == "RootWebArea"), {})
            self._landing_url = root.get("url") or base_url
            self._dashboard_status = "complete"
            if self._selected_modules - self._modules.keys():
                raise ValueError("Selected paths are no longer in the observed navigation catalog. Discover application paths again.")
            await self._save_checkpoint(force=True)
