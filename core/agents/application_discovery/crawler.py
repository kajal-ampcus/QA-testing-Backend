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

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

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

_NAVIGABLE_ROLES_AUTH = {"link", "button", "menuitem", "tab", "option", "treeitem"}
_NAVIGABLE_ROLES_UNAUTH = {"link"}  # links only — never click submit buttons in Phase 1

# Form-action names explicitly excluded from Phase 1 navigation
# even if they somehow appear as role=link
_FORM_ACTION_NAMES = {
    "log in", "login", "sign in", "create account",
    "send reset link", "submit", "↻", "refresh captcha",
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
            "uid": uid, "role": role, "name": name,
            "url": url_match.group(1) if url_match else None,
        })
    return elements


def _parse_console_messages(raw: object) -> list[dict[str, str]]:
    messages = []
    for line in _snapshot_text(raw).splitlines():
        line = line.strip()
        if not line:
            continue
        level = "error" if re.search(r"\berror\b", line, re.I) else (
            "warning" if re.search(r"\bwarn(ing)?\b", line, re.I) else "log"
        )
        messages.append({"level": level, "text": line[:500]})
    return messages


def _parse_network_requests(raw: object) -> list[dict[str, str]]:
    requests = []
    for line in _snapshot_text(raw).splitlines():
        line = line.strip()
        m = re.search(r'\b(GET|POST|PUT|PATCH|DELETE|OPTIONS)\s+(https?://\S+|\S+)', line, re.I)
        s = re.search(r'\b(\d{3})\b', line)
        if m:
            requests.append({"method": m.group(1).upper(), "url": m.group(2)[:300], "status": s.group(1) if s else "unknown"})
    return requests


def _is_external(url: str | None, base_url: str) -> bool:
    if not url:
        return False
    return urlparse(url).netloc not in ("", urlparse(base_url).netloc)


def _is_form_action(name: str) -> bool:
    return name.strip().lower() in _FORM_ACTION_NAMES


def _looks_like_login_page(text: str) -> bool:
    """True when snapshot text contains login page markers."""
    return bool(re.search(r'\b(sign\s*in|log\s*in|welcome\s*back|log\s*in\s*to)\b', text, re.I))


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

    async def _capture_debug_signals(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
        console_errors: list[dict[str, Any]] = []
        network_requests: list[dict[str, Any]] = []
        screenshot: str | None = None
        try:
            all_msgs = _parse_console_messages(await self._client.list_console_messages())
            console_errors = [m for m in all_msgs if m["level"] in ("error", "warning")]
        except Exception:
            pass
        try:
            network_requests = _parse_network_requests(await self._client.list_network_requests())
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
        classified: list[dict[str, Any]],
        console_errors: list[dict[str, Any]],
        network_requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = list(classified)
        base = len(classified) + 1
        for i, msg in enumerate(console_errors, start=base):
            result.append({"element_code": f"CON-{i:03d}", "role": "console",
                           "name": msg["text"][:200], "risk": "SAFE",
                           "source": EvidenceSource.OBSERVED_CONSOLE.value, "level": msg["level"]})
        for i, req in enumerate(network_requests, start=base):
            result.append({"element_code": f"NET-{i:03d}", "role": "network",
                           "name": f"{req['method']} {req['url']}", "risk": "SAFE",
                           "source": EvidenceSource.OBSERVED_NETWORK.value, "status": req.get("status", "unknown")})
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
            try:
                await self._client.clear_cookies()
            except Exception:
                pass

        candidates = []
        if self._login_url:
            candidates.append(self._login_url)
        candidates.append(urljoin(base_url.rstrip("/") + "/", "login"))
        candidates.append(base_url)

        for url in candidates:
            await self._client.navigate_page(url)
            await self._client.wait_until_ready()
            text = _snapshot_text(await self._client.take_snapshot())
            if _looks_like_login_page(text):
                print(f"[crawler] landed on login page at {url}")
                return await self._client.take_snapshot()
            print(f"[crawler] {url} did not look like login page, trying next")

        print(f"[crawler] WARNING: could not confirm login page — using last snapshot")
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
            elements = _parse_elements(snapshot)
            match = next(
                (el for el in elements if el["role"] == step.role and el["name"] == step.name),
                None,
            )
            if match is None or not match.get("uid"):
                raise RuntimeError(f"Replay failed: {step.role!r} '{step.name}' not found")
            await self._client.click(match["uid"])
            try:
                await self._client.handle_dialog("dismiss")
            except Exception:
                pass
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

        fingerprint = compute_fingerprint(state_url, _snapshot_text(snapshot))
        if fingerprint in self._visited_fingerprints:
            print(f"[crawler] Already visited: {state_url} (fingerprint match)")
            return None, []
        self._visited_fingerprints.add(fingerprint)

        raw_elements = _parse_elements(snapshot)
        if not raw_elements:
            print(f"[crawler] WARNING: snapshot at {state_url} had no elements — skipping state")
            return None, []

        classified = [
            {"element_code": f"EL-{i:03d}", "role": el["role"], "name": el["name"],
             "risk": classify_risk(el["role"], el["name"]).value, "source": EvidenceSource.OBSERVED_DOM.value}
            for i, el in enumerate(raw_elements, start=1)
        ]

        console_errors, network_requests, screenshot = await self._capture_debug_signals()
        all_elements = self._build_elements(classified, console_errors, network_requests)

        print(f"[crawler] Recorded state: {state_url} ({len(classified)} elements, path depth {len(path)})")
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
        actions_examined = 0
        termination_reason = "EXPLORATION_EXHAUSTED"

        # ── PHASE 1: Unauthenticated pages ──────────────────────────────────
        # Clear cookies → navigate to login page → record login/register/forgot-password
        # RULE: follow links only, never buttons (no form submissions, no CAPTCHA triggers)
        print("[crawler] === PHASE 1: Unauthenticated pages ===")
        try:
            # clear_session=True — destroy any cached cookie ONCE before Phase 1 starts
            unauth_snapshot = await self._navigate_to_login(base_url, clear_session=True)
            _, unauth_elements = await self._record_state(unauth_snapshot, [], on_state_discovered)
            if unauth_elements:
                pages_visited += 1

            unauth_queue: list[QueueItem] = []
            for el in unauth_elements:
                if el["role"] not in _NAVIGABLE_ROLES_UNAUTH:
                    continue
                if _is_form_action(el["name"]):
                    continue
                if classify_risk(el["role"], el["name"]) == RiskLevel.DESTRUCTIVE:
                    continue
                if _is_external(el.get("url"), base_url):
                    continue
                print(f"[crawler] Phase 1 queue: link '{el['name']}'")
                unauth_queue.append(QueueItem(
                    path=[ClickStep(role=el["role"], name=el["name"])],
                    score=_relevance_score(el["name"], self._keywords),
                    skip_auth=True,
                ))

            for item in unauth_queue:
                if pages_visited >= self._budget.max_pages:
                    break
                if time.monotonic() - start_time > self._budget.max_duration_seconds:
                    termination_reason = "MAX_DURATION_REACHED"
                    break
                actions_examined += 1
                try:
                    snap = await self._replay_to(item.path, skip_auth=True)
                    fingerprint, _ = await self._record_state(snap, item.path, on_state_discovered)
                    if fingerprint is not None:
                        pages_visited += 1
                except Exception as e:
                    print(f"[crawler] Phase 1 sub-page failed ({item.path}): {e}")
                    continue

        except Exception as e:
            # FIX 3: Log instead of silently swallow
            print(f"[crawler] Phase 1 failed entirely: {e}")

        # ── PHASE 2: Authenticated pages ─────────────────────────────────────
        print("[crawler] === PHASE 2: Authenticated pages ===")
        try:
            auth_snapshot = await self._go_to_auth_start()
        except Exception as e:
            print(f"[crawler] FATAL: Authentication failed: {e}")
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

            actions_examined += 1
            try:
                snapshot = await self._replay_to(item.path, skip_auth=False)
            except RuntimeError as e:
                print(f"[crawler] Phase 2 replay failed {item.path}: {e}")
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
            "actions_examined": actions_examined,
            "actions_remaining": len(auth_queue),
            "queue_exhausted": not auth_queue,
        }
        print(f"[crawler] Done. {pages_visited} states, termination={termination_reason}")
        return "PARTIAL" if termination_reason != "EXPLORATION_EXHAUSTED" else "COMPLETE"
