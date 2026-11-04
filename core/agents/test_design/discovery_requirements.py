"""Conservative page/requirement suggestions and evidence-only requirement drafts."""
import re
import json
from urllib.parse import urlsplit


def route_tag(state):
    return "discovery-route:" + (urlsplit(state.url_pattern).path.rstrip("/") or "/")


def page_terms(state):
    path = urlsplit(state.url_pattern).path
    headings = " ".join(str(e.get("name") or "") for e in state.elements
                        if e.get("role") == "heading" and not re.search(
                            r"\b(header|footer|banner|navigation|nav|contentinfo)\b",
                            str(e.get("locator_scope") or e.get("landmark") or ""), re.I))
    words = set(re.findall(r"[a-z0-9]{3,}", (path + " " + headings).lower()))
    return words - {"www", "http", "https", "html", "page", "welcome", "application", "com", "info"}


def matching_requirement(state, version):
    if route_tag(state) in (version.domain_tags or []):
        return "linked"
    if any(tag.startswith("discovery-route:") for tag in (version.domain_tags or [])):
        return None
    # Never use global header/sidebar controls as proof of feature coverage.
    text = " ".join([version.title, version.description,
                     *[str(ac.get("text") or "") for ac in version.acceptance_criteria]]).lower()
    text = re.sub(r"\blog\s+in\b|\bsign[ -]?in\b", "login", text)
    words = set(re.findall(r"[a-z0-9]{3,}", text))
    # The public entry page is also evidence for an explicit "open login"
    # criterion, but a Login link in every page's header is not coverage.
    path = urlsplit(state.url_pattern).path
    if re.fullmatch(r"/(?:[a-z]{2}(?:-[A-Za-z]{2})?/?)?", path) and (
        words & {"home", "homepage", "landing"}
        or ("login" in words and re.search(r"\b(click\w*|open\w*|navigate\w*)\b", text)
            and any(e.get("role") in {"button", "link"} and re.search(r"log\s*in|sign\s*in", str(e.get("name") or ""), re.I)
                    for e in state.elements))
    ):
        return "suggested"
    return "suggested" if page_terms(state) & words else None


def discovery_draft(states, exploratory=False, behavioral=False, success_outcome=None):
    criteria = []
    for state in states:
        local = [e for e in state.elements if not re.search(
            r"\b(header|footer|banner|navigation|nav|contentinfo)\b",
            str(e.get("locator_scope") or e.get("landmark") or ""), re.I)
            and not re.search(r"/(?:header|footer|nav)(?:/|\[|$)", str(e.get("xpath") or ""), re.I)]
        forms = [e for e in local if re.search(r"\bform\b", str(e.get("locator_scope") or e.get("landmark") or ""), re.I)
                 or re.search(r"/form(?:/|\[|$)", str(e.get("xpath") or ""), re.I)]
        if any(e.get("role") in {"textbox", "combobox", "radio", "checkbox", "spinbutton"} for e in forms):
            local = forms
        seen = set()
        for element in local:
            name, role = str(element.get("name") or "").strip(), element.get("role")
            if (not name or element.get("visible") is False
                    or role not in {"textbox", "combobox", "radio", "checkbox", "spinbutton", "button", "heading"}
                    or (name, role) in seen):
                continue
            seen.add((name, role))
            text = f"Observed control {json.dumps(name, ensure_ascii=False)} with role {role} is visible on {json.dumps(state.url_pattern)}."
            criteria.append({"id": f"AC-{len(criteria)+1}", "text": text, "source": "OBSERVED_DOM"})
        if not seen:
            criteria.append({"id": f"AC-{len(criteria)+1}", "text": f"The page {state.url_pattern} can be opened and its captured content reviewed.", "source": "OBSERVED_DOM"})
    label = ", ".join(state.url_pattern for state in states)
    if behavioral and not exploratory:
        from core.agents.test_design.form_contracts import behavioral_criteria
        criteria.extend(behavioral_criteria(states, success_outcome))
        for index, ac in enumerate(criteria, 1):
            ac["id"] = f"AC-{index}"
    return {"title": (("Exploratory checks: " if exploratory else "Discovered feature: ") + label)[:200],
            "description": "Review of observed UI for " + label + ". Confirm the intended business behavior before adding submission, persistence or notification assertions.",
            "acceptance_criteria": criteria,
            "ambiguities": [],
            "domain_tags": list(dict.fromkeys(route_tag(state) for state in states))
                + (["exploratory"] if exploratory else [])}
