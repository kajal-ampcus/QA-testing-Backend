"""Deterministic checks on generated cases before they can be persisted."""

import re
from typing import Any
from urllib.parse import urlparse

from core.agents.test_design.schemas import TestCaseSpec


def validate_case(case: TestCaseSpec, states: list[dict[str, Any]], ac_ids: set[str]) -> list[str]:
    issues: list[str] = []
    by_code = {s["state_code"]: s for s in states}
    if not case.steps or case.steps[-1].action != "assert":
        issues.append("End with an assertion verifying the result; a click alone is not a test.")
    if not set(case.traceability).issubset(ac_ids):
        issues.append("Traceability contains an unknown acceptance criterion.")
    previous_action_state = None
    submitted = False
    for number, step in enumerate(case.steps, 1):
        if step.step_number != number:
            issues.append(f"Step {number}: step numbers must be consecutive.")
        state = by_code.get(step.target.state_code)
        if state is None:
            issues.append(f"Step {number}: state is not observed.")
            continue
        element = next(
            (e for e in state["elements"] if e["element_code"] == step.target.element_code), None
        )
        if step.target.element_code and element is None:
            issues.append(f"Step {number}: element is not observed in this state.")
        if step.action in {"fill", "click"} and element is None:
            issues.append(f"Step {number}: {step.action} requires an observed element.")
        if element:
            for field, key in (("element_name", "name"), ("element_role", "role")):
                value = getattr(step.target, field)
                if value and value != element[key]:
                    issues.append(f"Step {number}: {field} differs from the observed element.")
            if step.action in {"fill", "click"} and (
                element.get("disabled") or element.get("visible") is False
            ):
                issues.append(
                    f"Step {number}: cannot act on this disabled/hidden control in this state."
                )
        for key in re.findall(r"\{([^{}]+)\}", step.value or ""):
            value: Any = case.test_data
            if key in value:
                continue
            for part in key.split("."):
                if not isinstance(value, dict) or part not in value:
                    issues.append(f"Step {number}: test_data path {{{key}}} cannot be resolved.")
                    break
                value = value[part]
        if step.action == "assert":
            expected = step.expected or ""
            if not expected.strip():
                issues.append(f"Step {number}: assertion needs an expected result.")
            if re.search(
                r"\b(note:|not mapped|not explicitly mapped|e\.g\.|implying|would verify)\b",
                expected,
                re.I,
            ):
                issues.append(
                    f"Step {number}: keep explanations out of the assertion's expected value."
                )
            if "url" in expected.lower():
                paths = re.findall(r"https?://[^\s'\"<>]+|(?<![\w/])/[\w/-]+", expected)
                for path in paths:
                    observed_path = state["url_pattern"]
                    expected_path = urlparse(path.rstrip(".,)")).path
                    if expected_path and expected_path not in observed_path:
                        issues.append(
                            f"Step {number}: expected URL does not match the target state's observed path."
                        )
            if (
                submitted
                and previous_action_state == step.target.state_code
                and element
                and element.get("role") in {"StaticText", "paragraph", "heading"}
                and expected.strip().strip("'\"") in element.get("name", "")
            ):
                issues.append(
                    f"Step {number}: pre-existing page text cannot prove that submission succeeded; use an observed result state."
                )
        elif step.action in {"click", "fill", "navigate"}:
            previous_action_state = step.target.state_code
            if step.action == "click" and element:
                submitted = bool(
                    re.search(r"\b(submit|send|save|log.?in|sign.?in)\b", element["name"], re.I)
                )
                if element.get("url"):
                    next_step = case.steps[number] if number < len(case.steps) else None
                    if next_step and next_step.action == "assert":
                        target_state = by_code.get(next_step.target.state_code)
                        href_path = urlparse(element["url"]).path
                        if target_state and href_path and href_path != target_state["url_pattern"]:
                            issues.append(
                                f"Step {number}: link destination and asserted state disagree; record a redirect if applicable."
                            )
    return list(dict.fromkeys(issues))
