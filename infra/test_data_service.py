"""Post-generation templates and form fixture resolution using existing models."""

import re
from copy import deepcopy
from typing import Any

from sqlalchemy import select

from core.agents.automation_generation.execution_plan import ground_steps
from domain.test_data_resolution import resolve_test_data
from infra.db.models.application_map import ApplicationMapState
from infra.db.models.form_test_data import FormTestData
from infra.db.models.test_case import TestCase, TestCaseVersion


async def resolve_case_data(session, project_id, case_id, generated):
    rows = list(await session.scalars(select(FormTestData).where(
        FormTestData.project_id == project_id,
        FormTestData.test_case_id == case_id,
        FormTestData.active.is_(True),
    )))
    if len(rows) > 1:
        raise ValueError("Multiple active fixtures are assigned to this test case")
    fixture = rows[0] if rows else None
    baseline = None
    if fixture:
        baselines = list(await session.scalars(select(FormTestData).where(
            FormTestData.project_id == project_id,
            FormTestData.test_case_id.is_(None),
            FormTestData.form_group == fixture.form_group,
            FormTestData.test_type == "positive",
            FormTestData.active.is_(True),
        )))
        # Ambiguous libraries are never resolved by arbitrary row ordering.
        if len(baselines) == 1:
            baseline = baselines[0].test_data
    generated = dict(generated or {})
    overrides = generated.pop("scenario_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("scenario_overrides must be an object")
    data, sources = resolve_test_data(generated, baseline, fixture.test_data if fixture else None, overrides)
    return data, sources, fixture


def field_requirements(steps, states):
    """Derive editable fields from actual case steps and stable map identifiers."""
    steps, _issues = ground_steps(deepcopy(steps or []), states, {})
    expanded = list(steps)
    reviewed_form = any((step.get("assertion") or {}).get("form_contract") == "reviewed" for step in steps)
    for step in steps:
        if reviewed_form:
            # These cases explicitly supply every companion input and every
            # fixed radio choice. Do not turn omitted ratings or fixed clicks
            # back into user-input requests through page-wide expansion.
            continue
        target = step.get("target") or {}
        state = states.get(target.get("state_code"))
        if state is None:
            continue
        element = next((item for item in state.elements if item.get("element_code") == target.get("element_code")), {})
        submission = step.get("action") == "click" and (
            element.get("input_type") == "submit"
            or (element.get("role") == "button" and re.search(r"\b(submit|send|save|register|apply|confirm)\b", str(element.get("name") or ""), re.I))
        )
        if step.get("action") not in {"fill", "select"} and not submission:
            continue
        # Collect companion fields on this observed page, without including
        # the site's header search, menu controls or a different known form.
        for item in state.elements:
            scope = str(item.get("locator_scope") or item.get("landmark") or "")
            if re.search(r"\b(header|footer|nav|banner|contentinfo|navigation|search)\b", scope, re.I):
                continue
            if element.get("form_key") and item.get("form_key") != element["form_key"]:
                continue
            if item.get("role") not in {"textbox", "input", "combobox", "checkbox", "radio", "spinbutton"}:
                continue
            if not scope and str(item.get("name") or "").strip().lower() == "search":
                continue
            if not item.get("element_code") or item.get("disabled") or item.get("readonly"):
                continue
            expanded.append({"action": "fill", "target": {"state_code": state.state_code, "element_code": item["element_code"]}})
    result = []
    seen = set()
    for step in expanded:
        if step.get("action") not in {"fill", "select"}:
            continue
        target = step.get("target") or {}
        state = states.get(target.get("state_code"))
        if state is None:
            continue
        matches = [element for element in state.elements
                   if element.get("element_code") == target.get("element_code")]
        if len(matches) != 1:
            continue
        element = matches[0]
        code = f"{state.state_code}/{element['element_code']}"
        if code in seen:
            continue
        seen.add(code)
        name = str(element.get("name") or target.get("element_name") or code)
        login_surface = bool(re.search(r"(?:/login|/sign[-_]?in)(?:[/#?]|$)", state.url_pattern, re.I)) or any(
            re.fullmatch(r"log\s*in|sign\s*in", str(item.get("name") or ""), re.I)
            and item.get("role") == "button" and any(
                candidate.get("input_type") == "password" for candidate in state.elements
            ) for item in state.elements
        )
        binding = element.get("credential_binding")
        raw = step.get("value")
        placeholder = re.fullmatch(r"\{([^{}]+)\}", raw) if isinstance(raw, str) else None
        key = placeholder.group(1) if placeholder else name
        runtime = bool((binding and login_surface) or key.startswith("credential:") or re.search(r"captcha|\botp\b", name, re.I)
                       or (login_surface and re.search(r"username|user name|e-?mail|password|user id", name, re.I)))
        result.append({
            "field_code": code, "field_key": key, "field_name": name,
            "field_type": element.get("input_type") or element.get("role") or "text",
            "page": state.url_pattern, "form_group": element.get("form_key") or state.state_code,
            "required": bool(element.get("required") or (element.get("validation") or {}).get("required") or re.search(r"[*＊]\s*$", name)), "options": element.get("options") or [],
            "runtime_only": runtime, "explicit_value": raw if not placeholder else None,
        })
    return result


def lookup(data: dict[str, Any], key: str):
    if key in data:
        return True, data[key]
    value = data
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


async def smart_template(session, project_id):
    pairs = list((await session.execute(select(TestCase, TestCaseVersion).join(
        TestCaseVersion, (TestCaseVersion.test_case_id == TestCase.id)
        & (TestCaseVersion.version == TestCase.current_version),
    ).where(TestCase.project_id == project_id).order_by(TestCase.tc_code))).all())
    map_ids = {case.application_map_id for case, _ in pairs}
    states = list(await session.scalars(select(ApplicationMapState).where(
        ApplicationMapState.application_map_id.in_(map_ids)))) if map_ids else []
    rows = []
    for case, version in pairs:
        data, sources, fixture = await resolve_case_data(session, project_id, case.id, version.test_data)
        mapped = {state.state_code: state for state in states if state.application_map_id == case.application_map_id}
        for field in field_requirements(version.steps, mapped):
            present, value = lookup(data, field["field_key"])
            present = present and value is not None
            source = sources.get(field["field_key"], "missing")
            if field["explicit_value"] is not None:
                present, value, source = True, field["explicit_value"], "override"
            # Templates expose metadata for credentials and challenges, never their values.
            if field["runtime_only"]:
                present, value, source = True, None, "runtime"
                field["explicit_value"] = None
            rows.append({**field, "test_case_id": str(case.id), "test_case_code": case.tc_code,
                         "scenario": version.category, "value": value, "missing": not present,
                         "source": source, "fixture_id": str(fixture.id) if fixture else None})
    return {"rows": rows, "case_count": len(pairs)}


async def runtime_form_values(session, project_id, scripts, bindings):
    """Resolve current fixture assignments before handing inputs to the runner.

    No vault values enter this bundle. Manual execution overrides are applied
    by the caller after these defaults.
    """
    values = {}
    for script in scripts:
        case = await session.get(TestCase, script.test_case_id)
        if case is None or case.project_id != project_id:
            raise ValueError("Runtime test case does not belong to this project")
        if case.current_version != script.test_case_version:
            raise ValueError("Regenerate automation after changing a test case")
        version = await session.scalar(select(TestCaseVersion).where(
            TestCaseVersion.test_case_id == case.id,
            TestCaseVersion.version == script.test_case_version,
        ))
        if version is None:
            raise ValueError("Runtime test case version is missing")
        data, sources, _fixture = await resolve_case_data(session, project_id, case.id, version.test_data)
        for binding in bindings:
            if binding.get("case_code") != case.tc_code or binding.get("explicit_value"):
                continue
            key = binding.get("data_key") or binding["field"]
            if key.startswith("credential:") or re.search(r"captcha|\botp\b", binding["field"], re.I):
                continue
            found, value = lookup(data, key)
            if found and isinstance(value, str) and (value or binding.get("allow_empty")):
                values[binding["key"]] = {"mode": "value" if value else "empty", "value": value,
                                           "source": sources.get(key, "case")}
    return values
