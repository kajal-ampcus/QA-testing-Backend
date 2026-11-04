"""Deterministic matching of a test case to an observed application-map state."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def _words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]{2,}", value.lower())
            if word not in {"the", "and", "for", "with", "from", "page", "screen", "verify", "test"}}


@dataclass
class PageMatch:
    state_code: str | None
    page_name: str | None
    confidence: float
    reason: str
    alternatives: list[dict[str, Any]]
    ambiguous: bool = False


class PageMatchingService:
    """Scores only states already present in the selected discovery map."""
    def match(self, case: dict[str, Any], states: list[Any]) -> PageMatch:
        hints = " ".join(str(case.get(key, "") or "") for key in (
            "title", "objective", "description", "preconditions", "steps", "expected_result",
            "page", "screen", "module", "url"))
        terms = _words(hints)
        scored: list[tuple[float, Any, list[str]]] = []
        for state in states:
            url = str(getattr(state, "url_pattern", "") or "")
            controls = " ".join(str(item.get("name", "")) for item in (getattr(state, "elements", []) or []))
            nav = " ".join(map(str, getattr(state, "reached_via", []) or []))
            name = self.page_name(state)
            fields = [("page/route", _words(f"{name} {url}"), 4), ("controls", _words(controls), 2), ("navigation", _words(nav), 1)]
            hits, possible, reasons = 0, 0, []
            for label, tokens, weight in fields:
                overlap = terms & tokens
                if tokens:
                    possible += weight
                if overlap:
                    hits += weight * min(1, len(overlap) / max(1, len(tokens) ** .5))
                    reasons.append(f"{label}: {', '.join(sorted(overlap)[:3])}")
            # Explicit URL/page hints should dominate ordinary text.
            explicit = _words(str(case.get("page", "")) + " " + str(case.get("screen", "")) + " " + str(case.get("url", "")))
            if explicit & _words(f"{name} {url}"):
                hits += 4
                possible += 4
            score = min(1.0, hits / max(1, possible))
            scored.append((score, state, reasons))
        scored.sort(key=lambda value: value[0], reverse=True)
        alternatives = [{"state_code": s.state_code, "page_name": self.page_name(s), "confidence": round(score * 100, 1)} for score, s, _ in scored[:3]]
        if not scored or scored[0][0] == 0:
            return PageMatch(None, None, 0, "No discovered page matched the case text.", alternatives)
        score, state, reasons = scored[0]
        ambiguous = len(scored) > 1 and score >= .55 and score - scored[1][0] < .12
        return PageMatch(state.state_code, self.page_name(state), round(score * 100, 1),
                         "; ".join(reasons) or "Matched discovered route.", alternatives, ambiguous)

    @staticmethod
    def page_name(state: Any) -> str:
        url = str(getattr(state, "url_pattern", "") or "")
        segment = url.rstrip("/").rsplit("/", 1)[-1] or "Home"
        return segment.replace("-", " ").replace("_", " ").title() + " Page"
