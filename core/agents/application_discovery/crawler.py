"""
Priority-queue crawl loop (architecture doc Section 9). Deterministic —
zero LLM calls during the crawl.

TWO-PHASE CRAWL:
  Phase 1 — unauthenticated: clear cookies, navigate to login_url, record
             login/register/forgot-password WITHOUT submitting any forms.
             Links only — never buttons — so CAPTCHA submit never fires.
  Phase 2 — authenticated: call authenticate() (fills credentials + CAPTCHA
             + submits), explore all post-login pages with full button/link nav.

ROOT CAUSE OF PREVIOUS REGRESSION (v20→v23, 6 states→2 states):
  _go_to_start(skip_auth=True) navigated to base_url which silently
  redirected to /dashboard because the isolated Chrome still held a session
  cookie from a previous run. Phase 1 recorded /dashboard (no links found),
  unauth_queue was empty, Phase 1 ended immediately. The bare
  'except Exception: pass' swallowed this silently.

FIXES IN THIS VERSION:
  1. clear_cookies() called before Phase 1 to destroy any cached session.
  2. login_url fallback: if credential has no login_url, we try base_url +
     "/login" before falling back to base_url itself.
  3. Phase 1 failure is now logged (printed) instead of silently swallowed.
  4. _record_state returns early with explicit log if snapshot has no elements.
"""

import json
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from core.agents.application_discovery.fingerprint import compute_fingerprint, normalize_url_pattern
from core.policy_safety.destructive_action_lexicon import classify_risk
from core.tool_gateway.gateway import BrowserInspection
from core.tool_gateway.snapshot import parse_elements
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
    url: str | None = None
    value: str | None = None


@dataclass
class QueueItem:
    path: list[ClickStep] = field(default_factory=list)
    score: float = 1.0
    skip_auth: bool = False


OnStateDiscovered = Callable[[dict[str, Any]], Awaitable[None]]

_NAVIGABLE_ROLES_AUTH = {"link", "button", "menuitem", "tab", "option", "treeitem"}
_NAVIGABLE_ROLES_UNAUTH = {"link"}  # links only — never click submit buttons in Phase 1

# Form-action names explicitly excluded from Phase 1 navigation
# even if they somehow appear as role=link
_FORM_ACTION_NAMES = {
    "log in",
    "login",
    "sign in",
    "create account",
    "send reset link",
    "submit",
    "↻",
    "refresh captcha",
}

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
    return parse_elements(snapshot)


def _parse_console_messages(raw: object) -> list[dict[str, str]]:
    messages = []
    for line in _snapshot_text(raw).splitlines():
        line = line.strip()
        if not line:
            continue
        level = (
            "error"
            if re.search(r"\berror\b", line, re.I)
            else ("warning" if re.search(r"\bwarn(ing)?\b", line, re.I) else "log")
        )
        messages.append({"level": level, "text": line[:500]})
    return messages


def _parse_network_requests(raw: object) -> list[dict[str, str]]:
    requests = []
    for line in _snapshot_text(raw).splitlines():
        line = line.strip()
        m = re.search(r"\b(GET|POST|PUT|PATCH|DELETE|OPTIONS)\s+(https?://\S+|\S+)", line, re.I)
        s = re.search(r"\b(\d{3})\b", line)
        if m:
            requests.append(
                {
                    "method": m.group(1).upper(),
                    "url": m.group(2)[:300],
                    "status": s.group(1) if s else "unknown",
                }
            )
    return requests


def _is_external(url: str | None, base_url: str) -> bool:
    if not url:
        return False
    return urlparse(url).netloc not in ("", urlparse(base_url).netloc)


def _is_form_action(name: str) -> bool:
    return name.strip().lower() in _FORM_ACTION_NAMES


def _looks_like_login_page(text: str) -> bool:
    """True when snapshot text contains login page markers."""
    return bool(re.search(r"\b(sign\s*in|log\s*in|welcome\s*back|log\s*in\s*to)\b", text, re.I))


class Crawler:
    def __init__(
        self,
        client: BrowserInspection,
        budget: CrawlBudget,
        keywords: list[str],
        login_url: str | None = None,
        authenticate: bool = False,
    ) -> None:
        self._client = client
        self._budget = budget
        self._keywords = keywords
        self._login_url = login_url
        self._authenticate = authenticate
        self._failures: list[dict[str, str]] = []
        self._skipped: set[str] = set()
        self._visited_fingerprints: set[str] = set()
        self.termination_reason: str = "EXPLORATION_EXHAUSTED"
        self.coverage: dict[str, Any] = {}

    def _resolve_login_url(self, base_url: str) -> str:
        """
        Return the best login URL to start from.
        Priority: stored credential login_url → base_url/login → base_url
        """
        if self._login_url:
            return self._login_url
        # Try appending /login to base_url as a sensible default
        candidate = urljoin(base_url.rstrip("/") + "/", "login")
        return candidate  # crawler will verify it's a login page after navigating

    async def _capture_debug_signals(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
        console_errors: list[dict[str, Any]] = []
        network_requests: list[dict[str, Any]] = []
        screenshot: str | None = None
        with suppress(Exception):
            all_msgs = _parse_console_messages(await self._client.list_console_messages())
            console_errors = [m for m in all_msgs if m["level"] in ("error", "warning")]
        with suppress(Exception):
            network_requests = _parse_network_requests(await self._client.list_network_requests())
        with suppress(Exception):
            s = _snapshot_text(await self._client.take_screenshot()).strip()
            if s:
                screenshot = s
        return console_errors, network_requests, screenshot

    def _build_elements(
        self,
        classified: list[dict[str, Any]],
        console_errors: list[dict[str, Any]],
        network_requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = list(classified)
        base = len(classified) + 1
        for i, msg in enumerate(console_errors, start=base):
            result.append(
                {
                    "element_code": f"CON-{i:03d}",
                    "role": "console",
                    "name": msg["text"][:200],
                    "risk": "SAFE",
                    "source": EvidenceSource.OBSERVED_CONSOLE.value,
                    "level": msg["level"],
                }
            )
        for i, req in enumerate(network_requests, start=base):
            result.append(
                {
                    "element_code": f"NET-{i:03d}",
                    "role": "network",
                    "name": f"{req['method']} {req['url']}",
                    "risk": "SAFE",
                    "source": EvidenceSource.OBSERVED_NETWORK.value,
                    "status": req.get("status", "unknown"),
                }
            )
        return result

    async def _navigate_to_login(self, base_url: str, clear_session: bool = False) -> object:
        """
        Navigate to the login page and verify we actually landed on it.
        Tries login_url first, then base_url/login, then base_url.

        clear_session=True  → called ONCE at the very start of Phase 1 to
                              destroy any cached cookie before first navigation.
        clear_session=False → called during sub-page replay; do NOT clear cookies
                              here because clearing mid-replay destroys the
                              navigation context and causes subsequent navigations
                              to fail or redirect unexpectedly.
        """
        if clear_session:
            with suppress(Exception):
                await self._client.clear_cookies()

        candidates = []
        if self._login_url:
            candidates.append(self._login_url)
        candidates.append(base_url)

        for url in dict.fromkeys(candidates):
            await self._client.navigate_page(url)
            await self._client.wait_until_ready()
            text = _snapshot_text(await self._client.take_snapshot())
            if _looks_like_login_page(text):
                print(f"[crawler] landed on login page at {url}")
                return await self._client.take_snapshot()
            print(f"[crawler] {url} did not look like login page, trying next")

        print("[crawler] WARNING: could not confirm login page — using last snapshot")
        return await self._client.take_snapshot()

    async def _go_to_auth_start(self) -> object:
        """Navigate to login, fill credentials + CAPTCHA, submit, return post-login snapshot."""
        login_url = self._login_url or urljoin(self._base_url.rstrip("/") + "/", "login")
        await self._client.navigate_page(login_url)
        await self._client.wait_until_ready()
        # authenticate() internally calls wait_until_ready() and solves CAPTCHA
        await self._client.authenticate()
        await self._client.wait_until_ready()
        return await self._client.take_snapshot()

    async def _replay_to(self, path: list[ClickStep], skip_auth: bool = False) -> object:
        if skip_auth:
            # clear_session=False — cookies already cleared at Phase 1 start;
            # clearing again mid-replay breaks subsequent navigations
            snapshot = await self._navigate_to_login(self._base_url, clear_session=False)
        else:
            snapshot = await self._go_to_auth_start()

        for step in path:
            if step.role == "link" and step.url:
                await self._client.navigate_page(step.url)
                await self._client.wait_until_ready()
                snapshot = await self._client.take_snapshot()
                continue
            elements = _parse_elements(snapshot)
            match = next(
                (
                    el
                    for el in elements
                    if el["role"] == step.role
                    and el["name"] == step.name
                    and (not step.url or el.get("url") == step.url)
                ),
                None,
            )
            if match is None or not match.get("uid"):
                raise RuntimeError(f"Replay failed: {step.role!r} '{step.name}' not found")
            if step.value is not None:
                await self._client.fill(match["uid"], step.value)
            else:
                await self._client.click(match["uid"])
            with suppress(Exception):
                await self._client.handle_dialog("dismiss")
            await self._client.wait_until_ready()
            snapshot = await self._client.take_snapshot()
        return snapshot

    async def _record_state(
        self,
        snapshot: object,
        path: list[ClickStep],
        on_state_discovered: OnStateDiscovered,
    ) -> tuple[str | None, list[dict[str, Any]]]:
        root = next((el for el in _parse_elements(snapshot) if el["role"] == "RootWebArea"), None)
        current_url = root.get("url") if root else None
        state_url = current_url or self._base_url

        if _is_external(current_url, self._base_url):
            print(f"[crawler] Skipping external URL: {current_url}")
            return None, []

        raw_elements = _parse_elements(snapshot)
        if hasattr(self._client, "inspect_elements"):
            try:
                raw_elements = await self._client.inspect_elements(snapshot)
            except Exception as exc:
                self._failures.append(
                    {"action": "inspect element attributes", "error": type(exc).__name__}
                )
        # Hash observable content, not transient MCP uids or URL fragments.
        semantic = [{k: v for k, v in el.items() if k != "uid"} for el in raw_elements]
        for el in semantic:
            if el.get("url"):
                el["url"] = urldefrag(el["url"])[0]
        fingerprint = compute_fingerprint(state_url, json.dumps(semantic, sort_keys=True))
        self._current_fingerprint = fingerprint
        if fingerprint in self._visited_fingerprints:
            print(f"[crawler] Already visited: {state_url} (fingerprint match)")
            # Phase 2 may start on the same public page as Phase 1. Keep its
            # controls available for the broader navigation policy without
            # persisting or counting that state twice.
            return None, _parse_elements(snapshot)
        if not raw_elements:
            print(f"[crawler] WARNING: snapshot at {state_url} had no elements — skipping state")
            return None, []
        self._visited_fingerprints.add(fingerprint)

        classified = [
            {
                **{k: v for k, v in el.items() if k != "uid"},
                "element_code": f"EL-{i:03d}",
                "risk": classify_risk(el["role"], el["name"]).value,
                "source": EvidenceSource.OBSERVED_DOM.value,
            }
            for i, el in enumerate(raw_elements, start=1)
        ]

        console_errors, network_requests, screenshot = await self._capture_debug_signals()
        all_elements = self._build_elements(classified, console_errors, network_requests)

        print(
            f"[crawler] Recorded state: {state_url} ({len(classified)} elements, path depth {len(path)})"
        )
        await on_state_discovered(
            {
                "url_pattern": normalize_url_pattern(state_url),
                "fingerprint": fingerprint,
                "reached_via": [
                    (
                        f"navigate(url={s.url!r},observed_link={s.name!r})"
                        if s.role == "link" and s.url
                        else f"{('fill' if s.value is not None else 'click')}(role={s.role},name={s.name!r}"
                        + (f",value={s.value!r}" if s.value is not None else "")
                        + ")"
                    )
                    for s in path
                ],
                "elements": all_elements,
                "evidence_ref": f"screenshot:{screenshot}" if screenshot else None,
            }
        )
        return fingerprint, raw_elements

    async def crawl(self, base_url: str, on_state_discovered: OnStateDiscovered) -> str:
        """Explore each observed state in each phase, recording honest limits."""
        self._base_url = base_url
        self._visited_fingerprints.clear()
        self._failures.clear()
        self._skipped.clear()
        queued_link_destinations: set[str] = set()
        started = time.monotonic()
        actions_examined = 0
        pending = 0
        depth_limited = 0
        termination = "EXPLORATION_EXHAUSTED"
        authenticated = False
        phases = [True, False] if self._authenticate else [True]
        for skip_auth in phases:
            if termination != "EXPLORATION_EXHAUSTED":
                break
            try:
                snapshot = (
                    await self._navigate_to_login(base_url, clear_session=True)
                    if skip_auth
                    else await self._go_to_auth_start()
                )
                if not skip_auth:
                    authenticated = True
                phase_path = (
                    []
                    if skip_auth
                    else [ClickStep(role="authentication", name="Log in")]
                )
                _, elements = await self._record_state(
                    snapshot, phase_path, on_state_discovered
                )
            except Exception as exc:
                self._failures.append(
                    {
                        "phase": "public" if skip_auth else "authenticated",
                        "error": type(exc).__name__,
                        "detail": re.sub(
                            r"(?i)(password|token|secret|api.?key)=[^\s&]+",
                            r"\1=<redacted>",
                            str(exc),
                        )[:300],
                    }
                )
                continue
            queue: list[QueueItem] = []
            expanded: set[str] = set()
            queued: set[tuple[str, str, str, str]] = set()

            def enqueue(
                nodes: list[dict[str, Any]],
                path: list[ClickStep],
                *,
                expanded: set[str] = expanded,
                queued: set[tuple[str, str, str, str]] = queued,
                queue: list[QueueItem] = queue,
                skip_auth: bool = skip_auth,
            ) -> None:
                nonlocal depth_limited
                if not nodes:
                    return
                fingerprint = self._current_fingerprint
                if fingerprint in expanded:
                    return
                expanded.add(fingerprint)
                root = next((e for e in nodes if e.get("role") == "RootWebArea"), {})
                current_url = root.get("url") or base_url
                for el in nodes:
                    role, name = el.get("role", ""), el.get("name", "")
                    if role not in {
                        "link",
                        "button",
                        "menuitem",
                        "tab",
                        "radio",
                        "checkbox",
                        "combobox",
                    }:
                        continue
                    if el.get("disabled") or el.get("visible") is False:
                        continue
                    destination = el.get("url")
                    if destination:
                        absolute = urljoin(current_url, destination)
                        if urlparse(absolute).scheme not in {"http", "https"} or _is_external(
                            absolute, base_url
                        ):
                            continue
                        if urldefrag(absolute)[0] == urldefrag(current_url)[0] and "#" in absolute:
                            continue
                        canonical_destination = urldefrag(absolute)[0].rstrip("/") or absolute
                        if canonical_destination in queued_link_destinations:
                            continue
                        queued_link_destinations.add(canonical_destination)
                    # Never send messages, submit forms, log out, or mutate records
                    # as an incidental discovery action. Authentication is explicit.
                    safe_link_navigation = role == "link" and bool(destination)
                    named_form_action = bool(
                        re.search(
                            r"\b(submit|send|subscribe|save|sign out|log out|logout|sign in|log in|login|register|create account)\b",
                            name,
                            re.I,
                        )
                    )
                    if (
                        classify_risk(role, name) == RiskLevel.DESTRUCTIVE
                        or (named_form_action and not safe_link_navigation)
                        or el.get("input_type") == "submit"
                    ):
                        self._skipped.add(f"{urlparse(current_url).path}: {role} {name}")
                        continue
                    values = [None]
                    if role == "combobox":
                        values = el.get("options", [])
                        if not values:
                            self._skipped.add(
                                f"{urlparse(current_url).path}: combobox {name} (options unavailable)"
                            )
                    for value in values:
                        key = (fingerprint, role, destination or name, str(value))
                        if key in queued:
                            continue
                        queued.add(key)
                        if len(path) >= self._budget.max_depth:
                            depth_limited += 1
                            continue
                        queue.append(
                            QueueItem(
                                path=[*path, ClickStep(role, name, destination, value)],
                                score=_relevance_score(name, self._keywords),
                                skip_auth=skip_auth,
                            )
                        )

            enqueue(elements, [])
            while queue:
                if len(self._visited_fingerprints) >= self._budget.max_pages:
                    termination = "MAX_PAGES_REACHED"
                    break
                if time.monotonic() - started >= self._budget.max_duration_seconds:
                    termination = "MAX_DURATION_REACHED"
                    break
                queue.sort(key=lambda item: item.score, reverse=True)
                item = queue.pop(0)
                actions_examined += 1
                try:
                    snapshot = await self._replay_to(item.path, skip_auth=skip_auth)
                    recorded_path = (
                        item.path
                        if skip_auth
                        else [ClickStep(role="authentication", name="Log in"), *item.path]
                    )
                    _, nodes = await self._record_state(
                        snapshot, recorded_path, on_state_discovered
                    )
                    enqueue(nodes, item.path)
                except Exception as exc:
                    self._failures.append(
                        {
                            "action": str(item.path[-1]),
                            "error": type(exc).__name__,
                            "detail": re.sub(
                                r"(?i)(password|token|secret|api.?key)=[^\s&]+",
                                r"\1=<redacted>",
                                str(exc),
                            )[:300],
                        }
                    )
            pending += len(queue)
        if termination == "EXPLORATION_EXHAUSTED":
            if self._failures:
                termination = "ACTION_FAILURES"
            elif depth_limited:
                termination = "MAX_DEPTH_REACHED"
        self.termination_reason = termination
        self.coverage = {
            "states_discovered": len(self._visited_fingerprints),
            "actions_examined": actions_examined,
            "actions_remaining": pending + depth_limited,
            "queue_exhausted": pending == 0 and depth_limited == 0,
            "failed_actions": self._failures,
            "skipped_actions": sorted(self._skipped),
            "authenticated_explored": authenticated,
            "scope": "Observed navigation and permitted controls; form submissions are not covered",
        }
        if not self._visited_fingerprints:
            return "FAILED"
        return "COMPLETE" if termination == "EXPLORATION_EXHAUSTED" else "PARTIAL"
