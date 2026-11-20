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
            "title", "objective", "description", "scenario_name", "preconditions", "steps", "step_notes", "expected_result",
            "page", "screen", "module", "url"))
        terms = _words(hints)
        start_hint = " ".join(str(case.get(key, "") or "") for key in ("title", "scenario_name", "preconditions", "step_notes", "page", "screen"))
        login_start = bool(re.search(r"\b(?:login|sign[ -]?in)\s+(?:page|screen|form)\b|\b(?:open|visit|navigate to)\s+(?:the\s+)?(?:login|sign[ -]?in)\b", start_hint, re.I))
        case_subject = " ".join(str(case.get(key, "") or "") for key in ("title", "scenario_name", "page", "screen"))
        scored: list[tuple[float, Any, list[str]]] = []
        for state in states:
            url = str(getattr(state, "url_pattern", "") or "")
            controls = " ".join(str(item.get("name", "")) for item in (getattr(state, "elements", []) or []))
            nav = " ".join(map(str, getattr(state, "reached_via", []) or []))
            name = self.page_name(state)
            fields = [("page/route", _words(f"{name} {url}"), 4), ("controls", _words(controls), 2), ("navigation", _words(nav), 1)]
            hits, possible, reasons = 0, sum(weight for _, _, weight in fields), []
            for label, tokens, weight in fields:
                overlap = terms & tokens
                if overlap:
                    # A partial page name such as "candidate" must not be
                    # treated as a match for "candidate submission".
                    contribution = (1.0 if tokens <= terms else 0.0) if label == 'page/route' else min(1, len(overlap) / max(1, len(tokens) ** .5))
                    hits += weight * contribution
                    if contribution:
                        reasons.append(f"{label}: {', '.join(sorted(overlap)[:3])}")
            # Explicit URL/page hints should dominate ordinary text.
            explicit = _words(str(case.get("page", "")) + " " + str(case.get("screen", "")) + " " + str(case.get("url", "")))
            if explicit & _words(f"{name} {url}"):
                hits += 4
                possible += 4
            if login_start:
                possible += 4
                if self._is_login(state):
                    hits += 4
                    reasons.append("observed login form matches starting page")
            # A supplied input is a page hint only when it exactly matches an
            # observed option. Never put the input value into diagnostics.
            input_hint = str(case.get('input') or '').strip().casefold()
            if input_hint:
                options = []
                for item in getattr(state, 'elements', []) or []:
                    validation = item.get('validation') if isinstance(item.get('validation'), dict) else {}
                    options.extend(item.get('options') or validation.get('options') or [])
                def option_label(option):
                    return str(option.get('label') or option.get('name') or option.get('value') or '') if isinstance(option, dict) else str(option)
                if any(option_label(option).strip().casefold() == input_hint for option in options):
                    hits += 2
                    possible += 2
                    reasons.append('input matches an observed option')
            score = min(1.0, hits / max(1, possible))
            if self._is_login(state) and not login_start and not re.search(r'\b(login|sign[ -]?in|authenticat\w*|credentials?)\b', case_subject, re.I):
                score = min(score, .49)
            scored.append((score, state, reasons))
        scored.sort(key=lambda value: value[0], reverse=True)
        alternatives = [{"state_code": s.state_code, "page_name": self.page_name(s), "confidence": round(score * 100, 1)} for score, s, _ in scored[:3]]
        if not scored or scored[0][0] == 0:
            return PageMatch(None, None, 0, "No discovered page matched the case text.", alternatives)
        score, state, reasons = scored[0]
        ambiguous = len(scored) > 1 and score - scored[1][0] < .12
        if score < .55:
            return PageMatch(None, None, round(score * 100, 1), 'No confident starting page; review the alternatives.', alternatives, True)
        return PageMatch(state.state_code, self.page_name(state), round(score * 100, 1),
                         "; ".join(reasons) or "Matched discovered route.", alternatives, ambiguous)

    @staticmethod
    def _is_login(state: Any) -> bool:
        elements = getattr(state, 'elements', []) or []
        return (any(str(item.get('input_type') or item.get('type') or '').lower() == 'password' for item in elements)
                and any(item.get('role') == 'button' and re.fullmatch(r'log\s*in|sign\s*in', str(item.get('name') or ''), re.I) for item in elements))

    @staticmethod
    def page_name(state: Any) -> str:
        if PageMatchingService._is_login(state):
            return 'Login Page'
        headings = [str(item.get('name') or '').strip() for item in (getattr(state, 'elements', []) or []) if item.get('role') == 'heading']
        if any(name.casefold() == 'dashboard' for name in headings):
            return 'Dashboard Page'
        url = str(getattr(state, "url_pattern", "") or "")
        segment = url.rstrip("/").rsplit("/", 1)[-1] or "Home"
        return segment.replace("-", " ").replace("_", " ").title() + " Page"
