"""Reviewable form rules and requirement-backed manual behavior designs.

UI markers suggest rules; approval confirms them. No native-validity or success
oracle is inferred from asterisks, and discovery never submits these forms.
"""
import re
from collections import defaultdict

from core.agents.test_design.fast_generate import _target, _observed
from core.agents.test_design.schemas import TestCaseSpec, TestStep


def display_name(name):
    name = re.sub(r"\s*\*\s*$", "", str(name or "")).strip()
    parts = name.split()
    if len(parts) % 2 == 0 and parts[:len(parts)//2] == parts[len(parts)//2:]:
        return " ".join(parts[:len(parts)//2])
    return name


def local_controls(state):
    return [e for e in state.elements if not re.search(
        r"\b(header|footer|banner|navigation|nav|contentinfo)\b",
        str(e.get("locator_scope") or e.get("landmark") or ""), re.I)
        and not re.search(r"/(?:header|footer|nav)(?:/|\[|$)", str(e.get("xpath") or ""), re.I)]


def form_profile(state):
    local = local_controls(state)
    if re.search(r"/(?:login|sign-in|signin)(?:/|$)", state.url_pattern, re.I) or any(
        e.get("input_type") == "password" for e in local):
        return None
    submits = [e for e in local if e.get("role") == "button" and (
        e.get("input_type") == "submit" or re.fullmatch(r"(?:submit|send|save)(?:\s+.+)?", str(e.get("name") or ""), re.I))]
    if len(submits) != 1:
        return None
    fields = [e for e in local if e.get("role") in {"textbox", "combobox", "spinbutton"}
              and not e.get("readonly") and e.get("visible") is not False and not e.get("disabled")]
    if not fields:
        return None
    groups = defaultdict(list)
    for e in local:
        if e.get("role") != "radio" or e.get("disabled") or e.get("visible") is False:
            continue
        key = e.get("input_name") or e.get("radio_group")
        xpath = str(e.get("xpath") or "")
        if not key and (match := re.match(r"(.*)/label(?:\[\d+\])?/.+", xpath)):
            key = match[1]
        if key:
            groups[str(key)].append(e)
    selections = []
    for options in groups.values():
        if len(options) < 2:
            continue
        position = local.index(options[0])
        before = local[max(0, position-6):position]
        labels = [str(e.get("name") or "").strip() for e in before if e.get("role") == "StaticText"]
        option_names = {display_name(e.get("name")) for e in options}
        label = next((display_name(name) for name in reversed(labels)
                      if name and name != "*" and display_name(name) not in option_names), "Option group")
        required = any(e.get("required") or (e.get("validation") or {}).get("required") for e in options)
        required = required or any("*" in name for name in labels)
        selections.append({"label": label, "options": options, "required": required})
    required = [e for e in fields if e.get("required") or (e.get("validation") or {}).get("required")
                or re.search(r"\*\s*$", str(e.get("name") or ""))]
    return {"fields": fields, "required": required, "groups": selections, "submit": submits[0]}


def visibility_only(version):
    return bool(version.acceptance_criteria) and all(
        str(ac.get("text") or "").startswith("Observed control ")
        or bool(re.fullmatch(r".+ (?:textbox|button|radio|input|form|fields|controls) (?:is|are) (?:visible|displayed)\.?",
                             str(ac.get("text") or ""), re.I))
        for ac in version.acceptance_criteria)


def behavioral_criteria(states, success_outcome=None):
    result = []
    for state in states:
        profile = form_profile(state)
        if not profile:
            continue
        page = state.url_pattern
        def add(text, kind, **extra):
            result.append({"text": text, "source": "INFERENCE", "form_behavior": {
                "kind": kind, "route": page, "approved_text": text, **extra}})
        for field in profile["fields"]:
            if field.get("role") == "textbox":
                add(f"On {page}, text entered in {display_name(field['name'])} is retained without changing the entered value.",
                    "entry", field=field["name"])
        for field in profile["required"]:
            add(f"On {page}, submitting with required {display_name(field['name'])} blank is blocked while the other inputs are valid.",
                "missing", field=field["name"])
        for group in profile["groups"]:
            names = [e["name"] for e in group["options"]]
            for option in group["options"]:
                add(f"On {page}, selecting {display_name(option['name'])} in {group['label']} selects that option and clears the other options in the same group.",
                    "choice", option=option["name"], options=names, group=group["label"])
            if group["required"]:
                add(f"On {page}, submitting without a required {group['label']} selection is blocked while the other inputs are valid.",
                    "missing_choice", options=names, group=group["label"])
        if profile["required"] or any(g["required"] for g in profile["groups"]):
            add(f"On {page}, submitting with all required inputs and selections empty is blocked.", "all_missing")
        if success_outcome:
            add(f"On {page}, submitting valid inputs and required selections using {display_name(profile['submit']['name'])} produces this result: {success_outcome.strip()}", "submit")
    return result


def contract_case(ac, state):
    """Return a case only for the unchanged reviewed contract and exact controls."""
    from types import SimpleNamespace
    rule = ac.get("form_behavior") or {}
    if rule.get("approved_text") != ac.get("text") or state.get("url_pattern") != rule.get("route"):
        return None
    profile = form_profile(SimpleNamespace(**state))
    if not profile:
        return None
    observed = _observed(state)
    if any(e not in observed for e in profile["fields"] + [profile["submit"]]):
        return None
    steps = [TestStep(step_number=1, action="navigate", target=_target(state))]
    data = {}
    kind = rule.get("kind")
    category = "NEGATIVE" if kind in {"missing", "missing_choice", "all_missing"} else "POSITIVE"
    title = ""
    def step(action, element, **kwargs):
        steps.append(TestStep(step_number=len(steps)+1, action=action, target=_target(state, element), **kwargs))
    if kind == "entry":
        matches = [e for e in profile["fields"] if e["name"] == rule.get("field") and e.get("role") == "textbox"]
        if len(matches) != 1:
            return None
        target = matches[0]
        key = "field_" + target["element_code"]
        data[key] = None
        step("fill", target, value="{" + key + "}")
        title = f"Enter and retain text in {display_name(target['name'])}"
    elif kind == "choice":
        matches = [g for g in profile["groups"] if [e["name"] for e in g["options"]] == rule.get("options")]
        if len(matches) != 1:
            return None
        options = [e for e in matches[0]["options"] if e["name"] == rule.get("option")]
        if len(options) != 1 or options[0] not in observed:
            return None
        target = options[0]
        step("click", target)
        title = f"Select {display_name(target['name'])} in {rule.get('group')}"
    elif kind in {"missing", "missing_choice", "all_missing", "submit"}:
        if kind == "missing" and len([e for e in profile["required"] if e["name"] == rule.get("field")]) != 1:
            return None
        if kind == "missing_choice" and not any([e["name"] for e in g["options"]] == rule.get("options") and g["required"] for g in profile["groups"]):
            return None
        for field in profile["fields"]:
            blank = (kind == "missing" and field["name"] == rule.get("field")) or (
                kind == "all_missing" and field in profile["required"])
            key = "field_" + field["element_code"]
            if not blank:
                data[key] = None
            step("select" if field.get("role") == "combobox" else "fill", field,
                 value="" if blank else "{" + key + "}")
        for group in profile["groups"]:
            if kind == "all_missing" or (kind == "missing_choice" and [e["name"] for e in group["options"]] == rule.get("options")):
                continue
            if any(e not in observed for e in group["options"]):
                return None
            step("click", group["options"][0])
        target = profile["submit"]
        step("click", target)
        title = {"missing": f"Reject submission with {display_name(rule.get('field'))} blank",
                 "missing_choice": f"Reject submission without {rule.get('group')}",
                 "all_missing": "Reject submission with all required inputs empty",
                 "submit": f"{display_name(profile['submit']['name'])} with valid inputs and required selections"}[kind]
    else:
        return None
    # The approved expected behavior is a manual oracle until result evidence
    # is captured. Do not upgrade it to native HTML validation or observed success.
    step("assert", target, expected=ac["text"], assertion={"kind": "manual", "target": target["element_code"],
         "text": ac["text"], "requirement_text": ac["text"], "basis": "valid", "form_contract": "reviewed"})
    return TestCaseSpec(title=title, objective=ac["text"], category=category,
        preconditions=[f"Browser is on {state['url_pattern']}",
            "Use configured valid form data for all inputs except the explicitly omitted value.",
            "Manual verification required: confirm the approved outcome; an automated result oracle has not been observed."],
        steps=steps, expected_result=ac["text"], test_data=data, traceability=[ac["id"]], confidence=0.7)
