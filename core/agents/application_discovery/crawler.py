"""
Priority-queue crawl loop (architecture doc Section 9). Deterministic —
zero LLM calls during the crawl (see the explanation in this conversation:
take_snapshot() is already structured data, not pixels, so there's no
perception problem for an LLM to solve here).

STATE MODEL: each queue item is a REPLAY PATH (an ordered list of
role+name click targets from the base URL), not a raw URL — chrome-devtools-mcp
element references (uid) are only valid against the snapshot they came from,
not stable across navigations. To explore a queued path: re-navigate to the
base URL, replay each click in order by re-snapshotting and matching
role+name at each step, then take a fresh snapshot at the destination and
enqueue its own candidate elements.

HONEST LIMITATIONS (flagged rather than silently assumed away):
- Role+name matching during replay is ambiguous if a page has two elements
  with the same role and name (e.g. two "Edit" buttons in a table) — this
  picks the first match. A real fix needs a more specific locator strategy
  than MVP scope covers.
- The current URL is read from the snapshot's RootWebArea. If a future MCP
  version omits it, the crawler falls back to the base URL.

Also owns the large-app sharding strategy (Section 9 scaling subsection):
MVP = one shard (the whole app, one CrawlBudget); each shard runs this exact
same algorithm scoped to its own budget and starting point.
"""

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


OnStateDiscovered = Callable[[dict[str, Any]], Awaitable[None]]


def _relevance_score(element_name: str, keywords: list[str]) -> float:
    """Deterministic keyword-overlap scoring against the requirement's own
    domain_tags/description — no LLM call. Neutral (0.5) with no requirement
    context, so an unscoped crawl still explores breadth-first rather than
    not exploring at all."""
    if not keywords:
        return 0.5
    name_words = set(element_name.lower().split())
    overlap = sum(1 for kw in keywords if kw.lower() in name_words)
    return min(1.0, 0.3 + 0.2 * overlap)


_SNAPSHOT_ELEMENT = re.compile(r'uid=(\S+)\s+(\S+)\s+"([^"]*)"')
_SNAPSHOT_URL = re.compile(r'url="([^"]+)"')


def _snapshot_text(snapshot: object) -> str:
    if isinstance(snapshot, list):
        return "\n".join(getattr(block, "text", "") for block in snapshot)
    return str(snapshot)


def _parse_elements(snapshot: object) -> list[dict[str, Any]]:
    """Extracts observed accessibility elements from a live MCP snapshot."""
    if isinstance(snapshot, dict):
        return [
            {
                "uid": node.get("uid"),
                "role": node.get("role", "unknown"),
                "name": node.get("name", ""),
            }
            for node in snapshot.get("elements", [])
        ]
    elements = []
    for line in _snapshot_text(snapshot).splitlines():
        match = _SNAPSHOT_ELEMENT.search(line)
        if match is None:
            continue
        uid, role, name = match.groups()
        url_match = _SNAPSHOT_URL.search(line)
        elements.append(
            {
                "uid": uid,
                "role": role,
                "name": name,
                "url": url_match.group(1) if url_match else None,
            }
        )
    return elements


class Crawler:
    def __init__(
        self, client: BrowserInspection, budget: CrawlBudget, keywords: list[str]
    ) -> None:
        self._client = client
        self._budget = budget
        self._keywords = keywords
        self._visited_fingerprints: set[str] = set()

    async def _replay_to(self, base_url: str, path: list[ClickStep]) -> object:
        """Navigates to base_url, replays each click by role+name matching."""
        await self._client.navigate_page(base_url)
        snapshot = await self._client.take_snapshot()
        for step in path:
            elements = _parse_elements(snapshot)
            match = next(
                (el for el in elements if el["role"] == step.role and el["name"] == step.name),
                None,
            )
            if match is None or not match["uid"]:
                raise RuntimeError(
                    f"Replay failed: could not find {step.role} '{step.name}' — "
                    f"the page may have changed since this path was discovered."
                )
            await self._client.click(match["uid"])
            snapshot = await self._client.take_snapshot()
        return snapshot

    async def crawl(self, base_url: str, on_state_discovered: OnStateDiscovered) -> str:
        """Runs the crawl. Calls on_state_discovered(state_dict) for each
        newly discovered, deduplicated state — the caller (agent.py) owns
        persistence, keeping this class free of DB/session concerns
        (docs/PROJECT_STRUCTURE.md's layering). Returns "COMPLETE" or
        "PARTIAL" depending on whether the budget was hit first."""
        start_time = time.monotonic()
        queue: list[QueueItem] = [QueueItem(path=[], score=1.0)]
        pages_visited = 0
        hit_budget = False

        while queue:
            if pages_visited >= self._budget.max_pages:
                hit_budget = True
                break
            if time.monotonic() - start_time > self._budget.max_duration_seconds:
                hit_budget = True
                break

            queue.sort(key=lambda item: item.score, reverse=True)
            item = queue.pop(0)
            if len(item.path) > self._budget.max_depth:
                continue

            try:
                snapshot = await self._replay_to(base_url, item.path)
            except RuntimeError:
                if not item.path:
                    raise
                continue  # this path no longer resolves — skip it, don't crash the whole crawl

            root = next(
                (el for el in _parse_elements(snapshot) if el["role"] == "RootWebArea"),
                None,
            )
            current_url = root.get("url") if root else None
            if current_url and urlparse(current_url).netloc != urlparse(base_url).netloc:
                continue
            state_url = current_url or base_url

            fingerprint = compute_fingerprint(state_url, _snapshot_text(snapshot))
            if fingerprint in self._visited_fingerprints:
                continue
            self._visited_fingerprints.add(fingerprint)
            pages_visited += 1

            elements = _parse_elements(snapshot)
            if not elements:
                raise RuntimeError("Chrome DevTools snapshot contained no identifiable elements")
            classified = [
                {
                    "element_code": f"EL-{index:03d}",
                    "role": el["role"],
                    "name": el["name"],
                    "risk": classify_risk(el["role"], el["name"]).value,
                    "source": EvidenceSource.OBSERVED_DOM.value,
                }
                for index, el in enumerate(elements, start=1)
            ]

            await on_state_discovered(
                {
                    "url_pattern": normalize_url_pattern(state_url),
                    "fingerprint": fingerprint,
                    "reached_via": [f"click(role={s.role},name={s.name!r})" for s in item.path],
                    "elements": classified,
                }
            )

            for el in elements:
                risk = classify_risk(el["role"], el["name"])
                # Destructive elements are recorded above (in `classified`)
                # but NEVER auto-clicked to continue the crawl (Section 29).
                if risk == RiskLevel.DESTRUCTIVE or el["role"] not in ("link", "button"):
                    continue
                target_url = el.get("url")
                if target_url and urlparse(target_url).netloc not in (
                    "", urlparse(base_url).netloc
                ):
                    continue
                score = _relevance_score(el["name"], self._keywords)
                queue.append(
                    QueueItem(
                        path=[*item.path, ClickStep(role=el["role"], name=el["name"])],
                        score=score,
                    )
                )

        return "PARTIAL" if hit_budget else "COMPLETE"
