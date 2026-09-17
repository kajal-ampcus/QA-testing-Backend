"""
Priority-queue crawl loop (architecture doc Section 9). Deterministic —
zero LLM calls during the crawl.

TWO-PHASE CRAWL:
  Phase 1 — unauthenticated: navigate to login_url, record login/register/
             forgot-password pages WITHOUT filling any forms or clicking
             submit buttons. Only follow genuine navigation links.
  Phase 2 — authenticated: call authenticate() which fills credentials +
             CAPTCHA and submits. Explore all post-login pages.

KEY RULE: During Phase 1 (unauthenticated), the crawler NEVER clicks
form submit buttons (Log in, Create account, Send reset link) or CAPTCHA
refresh buttons (↻). These would cause form-submission states (e.g.
"incorrect captcha answer") to be recorded as distinct states, polluting
the map. Only role=link elements are followed in Phase 1.
"""

import contextlib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from core.agents.application_discovery.fingerprint import compute_fingerprint, normalize_url_pattern
from core.policy_safety.destructive_action_lexicon import classify_risk
from core.tool_gateway.gateway import BrowserInspection
from domain.enums import EvidenceSource, RiskLevel


@dataclass
class CrawlBudget:
    max_pages: int = 150
    max_depth: int = 6
    max_duration_seconds: int = 900


@dataclass
class ClickStep:
    role: str
    name: str


@dataclass
class QueueItem:
    path: list[ClickStep] = field(default_factory=list)
    score: float = 1.0
    skip_auth: bool = False


OnStateDiscovered = Callable[[dict[str, Any]], Awaitable[None]]

# Roles navigated during AUTHENTICATED phase (full set)
_NAVIGABLE_ROLES_AUTH = {"link", "button", "menuitem", "tab", "option", "treeitem"}

# Roles navigated during UNAUTHENTICATED phase — links ONLY.
# Buttons on login/register pages are form submit buttons and CAPTCHA controls.
# Following them pollutes the map with error states ("incorrect captcha answer").
_NAVIGABLE_ROLES_UNAUTH = {"link"}

# Names that indicate a form submit or CAPTCHA control — never follow these
# during unauthenticated crawl even if role=link slips through
_FORM_ACTION_NAMES = {
    "log in", "login", "sign in", "create account", "register",
    "send reset link", "submit", "↻", "refresh captcha",
}

# Interactive roles — recorded but not clicked
_INTERACTIVE_ROLES = {"textbox", "combobox", "searchbox", "spinbutton"}


def _relevance_score(element_name: str, keywords: list[str]) -> float:
    if not keywords:
        return 0.5
    name_lower = element_name.lower()
    overlap = sum(1 for kw in keywords if kw.lower() in name_lower)
    return min(1.0, 0.3 + 0.2 * overlap)


_SNAPSHOT_ELEMENT = re.compile(r'uid=(\S+)\s+(\S+)\s+"([^"]*)"')
_SNAPSHOT_URL = re.compile(r'url="([^"]+)"')


def _snapshot_text(snapshot: object) -> str:
    if isinstance(snapshot, list):
        return "\n".join(getattr(block, "text", "") for block in snapshot)
    return str(snapshot)


def _parse_elements(snapshot: object) -> list[dict[str, Any]]:
    if isinstance(snapshot, dict):
        return [
            {"uid": node.get("uid"), "role": node.get("role", "unknown"), "name": node.get("name", "")}
            for node in snapshot.get("elements", [])
        ]
    elements = []
    for line in _snapshot_text(snapshot).splitlines():
        match = _SNAPSHOT_ELEMENT.search(line)
        if match is None:
            continue
        uid, role, name = match.groups()
        url_match = _SNAPSHOT_URL.search(line)
        elements.append({
            "uid": uid,
            "role": role,
            "name": name,
            "url": url_match.group(1) if url_match else None,
        })
    return elements


def _parse_console_messages(raw: object) -> list[dict[str, str]]:
    text = _snapshot_text(raw)
    messages = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        level = "log"
        if re.search(r"\berror\b", line, re.I):
            level = "error"
        elif re.search(r"\bwarn(ing)?\b", line, re.I):
            level = "warning"
        messages.append({"level": level, "text": line[:500]})
    return messages


def _parse_network_requests(raw: object) -> list[dict[str, str]]:
    text = _snapshot_text(raw)
    requests = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.search(r'\b(GET|POST|PUT|PATCH|DELETE|OPTIONS)\s+(https?://\S+|\S+)', line, re.I)
        status_match = re.search(r'\b(\d{3})\b', line)
        if match:
            requests.append({
                "method": match.group(1).upper(),
                "url": match.group(2)[:300],
                "status": status_match.group(1) if status_match else "unknown",
            })
    return requests


def _is_external(url: str | None, base_url: str) -> bool:
    if not url:
        return False
    return urlparse(url).netloc not in ("", urlparse(base_url).netloc)


def _is_form_action(name: str) -> bool:
    """True if this element name looks like a form submit or CAPTCHA control."""
    return name.strip().lower() in _FORM_ACTION_NAMES


class Crawler:
    def __init__(
        self,
        client: BrowserInspection,
        budget: CrawlBudget,
        keywords: list[str],
        login_url: str | None = None,
    ) -> None:
        self._client = client
        self._budget = budget
        self._keywords = keywords
        self._login_url = login_url
        self._visited_fingerprints: set[str] = set()
        self.termination_reason: str = "EXPLORATION_EXHAUSTED"
        self.coverage: dict[str, Any] = {}

    async def _capture_debug_signals(self) -> tuple[list[dict], list[dict], str | None]:
        console_errors: list[dict] = []
        network_requests: list[dict] = []
        screenshot: str | None = None
        try:
            raw = await self._client.list_console_messages()
            all_msgs = _parse_console_messages(raw)
            console_errors = [m for m in all_msgs if m["level"] in ("error", "warning")]
        except Exception:
            pass
        try:
            raw = await self._client.list_network_requests()
            network_requests = _parse_network_requests(raw)
        except Exception:
            pass
        try:
            s = _snapshot_text(await self._client.take_screenshot()).strip()
            if s:
                screenshot = s
        except Exception:
            pass
        return console_errors, network_requests, screenshot

    def _build_elements(
        self,
        classified: list[dict],
        console_errors: list[dict],
        network_requests: list[dict],
    ) -> list[dict]:
        result = list(classified)
        for i, msg in enumerate(console_errors, start=len(classified) + 1):
            result.append({
                "element_code": f"CON-{i:03d}", "role": "console",
                "name": msg["text"][:200], "risk": "SAFE",
                "source": EvidenceSource.OBSERVED_CONSOLE.value, "level": msg["level"],
            })
        for i, req in enumerate(network_requests, start=len(classified) + 1):
            result.append({
                "element_code": f"NET-{i:03d}", "role": "network",
                "name": f"{req['method']} {req['url']}", "risk": "SAFE",
                "source": EvidenceSource.OBSERVED_NETWORK.value, "status": req.get("status", "unknown"),
            })
        return result

    async def _go_to_start(self, skip_auth: bool = False) -> object:
        start = self._login_url or self._base_url
        await self._client.navigate_page(start)
        await self._client.wait_until_ready()
        if not skip_auth:
            # authenticate() calls wait_until_ready() internally before reading snapshot
            await self._client.authenticate()
            await self._client.wait_until_ready()
        return await self._client.take_snapshot()

    async def _replay_to(self, path: list[ClickStep], skip_auth: bool = False) -> object:
        snapshot = await self._go_to_start(skip_auth=skip_auth)
        for step in path:
            elements = _parse_elements(snapshot)
            match = next(
                (el for el in elements if el["role"] == step.role and el["name"] == step.name),
                None,
            )
            if match is None or not match.get("uid"):
                raise RuntimeError(f"Replay failed: could not find {step.role!r} '{step.name}'")
            await self._client.click(match["uid"])
            with contextlib.suppress(Exception):
                await self._client.handle_dialog("dismiss")
            await self._client.wait_until_ready()
            snapshot = await self._client.take_snapshot()
        return snapshot

    async def _record_state(
        self,
        snapshot: object,
        path: list[ClickStep],
        on_state_discovered: OnStateDiscovered,
    ) -> tuple[str | None, list[dict]]:
        root = next(
            (el for el in _parse_elements(snapshot) if el["role"] == "RootWebArea"), None
        )
        current_url = root.get("url") if root else None
        state_url = current_url or self._base_url

        if _is_external(current_url, self._base_url):
            return None, []

        fingerprint = compute_fingerprint(state_url, _snapshot_text(snapshot))
        if fingerprint in self._visited_fingerprints:
            return None, []
        self._visited_fingerprints.add(fingerprint)

        raw_elements = _parse_elements(snapshot)
        classified = [
            {
                "element_code": f"EL-{i:03d}",
                "role": el["role"],
                "name": el["name"],
                "risk": classify_risk(el["role"], el["name"]).value,
                "source": EvidenceSource.OBSERVED_DOM.value,
            }
            for i, el in enumerate(raw_elements, start=1)
        ]

        console_errors, network_requests, screenshot = await self._capture_debug_signals()
        all_elements = self._build_elements(classified, console_errors, network_requests)

        await on_state_discovered({
            "url_pattern": normalize_url_pattern(state_url),
            "fingerprint": fingerprint,
            "reached_via": [f"click(role={s.role},name={s.name!r})" for s in path],
            "elements": all_elements,
            "evidence_ref": f"screenshot:{screenshot}" if screenshot else None,
        })

        return fingerprint, raw_elements

    async def crawl(self, base_url: str, on_state_discovered: OnStateDiscovered) -> str:
        self._base_url = base_url
        start_time = time.monotonic()
        pages_visited = 0
        termination_reason = "EXPLORATION_EXHAUSTED"

        # ── PHASE 1: Unauthenticated pages ──────────────────────────────────
        # Record login page and follow LINKS ONLY (not buttons).
        # This discovers /register and /forgot-password without submitting any form.
        try:
            unauth_snapshot = await self._go_to_start(skip_auth=True)
            _, unauth_elements = await self._record_state(unauth_snapshot, [], on_state_discovered)
            if unauth_elements:
                pages_visited += 1

            unauth_queue: list[QueueItem] = []
            for el in unauth_elements:
                # PHASE 1 RULE: links only — never buttons (avoids form submissions + CAPTCHA refresh)
                if el["role"] not in _NAVIGABLE_ROLES_UNAUTH:
                    continue
                if _is_form_action(el["name"]):
                    continue
                if classify_risk(el["role"], el["name"]) == RiskLevel.DESTRUCTIVE:
                    continue
                if _is_external(el.get("url"), base_url):
                    continue
                unauth_queue.append(QueueItem(
                    path=[ClickStep(role=el["role"], name=el["name"])],
                    score=_relevance_score(el["name"], self._keywords),
                    skip_auth=True,
                ))

            # Explore one level deep from each unauth page (links only)
            for item in unauth_queue:
                if pages_visited >= self._budget.max_pages:
                    break
                if time.monotonic() - start_time > self._budget.max_duration_seconds:
                    termination_reason = "MAX_DURATION_REACHED"
                    break
                try:
                    snap = await self._replay_to(item.path, skip_auth=True)
                    _, _ = await self._record_state(snap, item.path, on_state_discovered)
                    pages_visited += 1
                except Exception:
                    continue

        except Exception:
            pass  # unauthenticated phase non-fatal

        # ── PHASE 2: Authenticated pages ─────────────────────────────────────
        # authenticate() fills email + password + CAPTCHA and submits.
        # This is where the CAPTCHA solver runs — never in Phase 1.
        try:
            auth_snapshot = await self._go_to_start(skip_auth=False)
        except Exception:
            self.termination_reason = "AUTHENTICATION_FAILED"
            self.coverage = {"states_discovered": pages_visited, "actions_examined": 0, "queue_exhausted": False}
            return "FAILED"

        _, auth_elements = await self._record_state(auth_snapshot, [], on_state_discovered)
        if auth_elements:
            pages_visited += 1

        auth_queue: list[QueueItem] = []
        for el in auth_elements:
            if el["role"] not in _NAVIGABLE_ROLES_AUTH:
                continue
            if classify_risk(el["role"], el["name"]) == RiskLevel.DESTRUCTIVE:
                continue
            if _is_external(el.get("url"), base_url):
                continue
            auth_queue.append(QueueItem(
                path=[ClickStep(role=el["role"], name=el["name"])],
                score=_relevance_score(el["name"], self._keywords),
                skip_auth=False,
            ))

        while auth_queue:
            if pages_visited >= self._budget.max_pages:
                termination_reason = "MAX_PAGES_REACHED"
                break
            if time.monotonic() - start_time > self._budget.max_duration_seconds:
                termination_reason = "MAX_DURATION_REACHED"
                break

            auth_queue.sort(key=lambda item: item.score, reverse=True)
            item = auth_queue.pop(0)
            if len(item.path) > self._budget.max_depth:
                continue

            try:
                snapshot = await self._replay_to(item.path, skip_auth=False)
            except RuntimeError:
                continue

            fingerprint, elements = await self._record_state(snapshot, item.path, on_state_discovered)
            if fingerprint is None:
                continue
            pages_visited += 1

            for el in elements:
                if el["role"] not in _NAVIGABLE_ROLES_AUTH:
                    continue
                if classify_risk(el["role"], el["name"]) == RiskLevel.DESTRUCTIVE:
                    continue
                if _is_external(el.get("url"), base_url):
                    continue
                auth_queue.append(QueueItem(
                    path=[*item.path, ClickStep(role=el["role"], name=el["name"])],
                    score=_relevance_score(el["name"], self._keywords),
                    skip_auth=False,
                ))

        self.termination_reason = termination_reason
        self.coverage = {
            "states_discovered": pages_visited,
            "actions_examined": len(auth_queue),
            "queue_exhausted": not auth_queue,
        }
        return "PARTIAL" if termination_reason != "EXPLORATION_EXHAUSTED" else "COMPLETE"
