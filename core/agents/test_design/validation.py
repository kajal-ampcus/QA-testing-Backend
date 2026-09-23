"""Deterministic checks on generated cases before they can be persisted.

Changes vs original:
  - Step number check moved AFTER renumbering (agent.py calls _renumber_steps
    before this), so gaps in model output no longer reject valid cases.
  - Traceability check reports the bad ids instead of a generic message.
  - Unknown state/element issues are collected but do not abort the loop.
"""

import re
from typing import Any
from urllib.parse import urlparse

from core.agents.test_design.schemas import TestCaseSpec


def validate_case(
    case: TestCaseSpec, states: list[dict[str, Any]], ac_ids: set[str]
) -> list[str]:
    issues: list[str] = []
    by_code = {s["state_code"]: s for s in states}

    if not case.steps:
        issues.append("Test case has no steps.")
        return issues

    # Step numbers are renumbered by _renumber_steps in agent.py before this
    # call, so they should always be consecutive starting at 1.
    for expected, step in enumerate(case.steps, 1):
        if step.step_number != expected:
            issues.append(
                f"Step {expected}: step_number={step.step_number} after renumbering."
            )

    if case.steps[-1].action != "assert":
        issues.append(
            "Last step must be an assert verifying the expected result; "
            "a click or fill alone is not a complete test."
        )

    bad_acs = set(case.traceability) - ac_ids
    if bad_acs:
        issues.append(f"Traceability contains unknown AC ids: {sorted(bad_acs)}.")

    previous_action_state = None
    submitted = False
    for number, step in enumerate(case.steps, 1):
        state = by_code.get(step.target.state_code)
        if state is None:
            issues.append(f"Step {number}: state_code '{step.target.state_code}' not in map.")
            continue

        element = next(
            (
                e
                for e in state.get("elements", [])
                if e.get("element_code") == step.target.element_code
            ),
            None,
        )

        if step.target.element_code and element is None:
            issues.append(
                f"Step {number}: element_code '{step.target.element_code}' "
                f"not in state '{step.target.state_code}'."
            )

        if step.action in {"fill", "click"} and step.target.element_code and element is None:
            issues.append(
                f"Step {number}: {step.action} requires an observed element."
            )

        if element:
            for field, key in (("element_name", "name"), ("element_role", "role")):
                value = getattr(step.target, field)
                if value and value != element.get(key):
                    issues.append(
                        f"Step {number}: {field} '{value}' differs from observed '{element.get(key)}'."
                    )
            if step.action in {"fill", "click"} and (
                element.get("disabled") or element.get("visible") is False
            ):
                issues.append(
                    f"Step {number}: cannot act on disabled/hidden element."
                )

        # Validate test_data placeholder resolution
        for key in re.findall(r"\{([^{}]+)\}", step.value or ""):
            data: Any = case.test_data
            if key in data:
                continue
            for part in key.split("."):
                if not isinstance(data, dict) or part not in data:
                    issues.append(
                        f"Step {number}: test_data path {{{key}}} cannot be resolved."
                    )
                    break
                data = data[part]

        if step.action == "assert":
            expected_text = (step.expected or "").strip()
            if not expected_text:
                issues.append(f"Step {number}: assert step has no expected value.")
            if re.search(
                r"\b(note:|not mapped|not explicitly mapped|e\.g\.|implying|would verify)\b",
                expected_text,
                re.I,
            ):
                issues.append(
                    f"Step {number}: keep explanations out of assert expected value."
                )
            if "url" in expected_text.lower() and state:
                paths = re.findall(
                    r"https?://[^\s'\"<>]+|(?<![\w/])/[\w/-]+", expected_text
                )
                for path in paths:
                    observed_path = state.get("url_pattern", "")
                    expected_path = urlparse(path.rstrip(".,)")).path
                    if expected_path and expected_path not in observed_path:
                        issues.append(
                            f"Step {number}: expected URL path '{expected_path}' does not "
                            f"match observed state path '{observed_path}'."
                        )
            if (
                submitted
                and previous_action_state == step.target.state_code
                and element
                and element.get("role") in {"StaticText", "paragraph", "heading"}
                and (step.expected or "").strip().strip("'\"") in element.get("name", "")
            ):
                issues.append(
                    f"Step {number}: pre-existing page text cannot prove submission succeeded; "
                    "use an observed result state."
                )
        elif step.action in {"click", "fill", "navigate"}:
            previous_action_state = step.target.state_code
            if step.action == "click" and element:
                submitted = bool(
                    re.search(
                        r"\b(submit|send|save|log.?in|sign.?in)\b",
                        element.get("name", ""),
                        re.I,
                    )
                )
                if element.get("url"):
                    next_step = (
                        case.steps[number] if number < len(case.steps) else None
                    )
                    if next_step and next_step.action == "assert":
                        target_state = by_code.get(next_step.target.state_code)
                        href_path = urlparse(element["url"]).path
                        if (
                            target_state
                            and href_path
                            and href_path != target_state.get("url_pattern", "")
                        ):
                            issues.append(
                                f"Step {number}: link destination and asserted state disagree."
                            )

    return list(dict.fromkeys(issues))  # deduplicate while preserving order
