"""
Build a Playwright TypeScript Page Object suite from approved cases and the
observed application map. Selector expressions come from selector_strategy.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from core.agents.automation_generation import templates
from core.agents.automation_generation.artifacts import list_files
from core.agents.automation_generation.execution_plan import prepare_execution
from core.agents.automation_generation.selector_strategy import (
    SelectorDecision,
    resolve_in_state,
)

_SENSITIVE = re.compile(
    r"password|passwd|secret|token|api[_-]?key|credential|session|authorization|private[_-]?key|\bpwd\b",
    re.IGNORECASE,
)
_RESERVED = {
    "page",
    "constructor",
    "open",
    "expect",
    "test",
    "default",
    "switch",
    "delete",
    "await",
    "class",
    "function",
    "return",
    "break",
    "case",
    "catch",
    "const",
    "continue",
    "else",
    "enum",
    "export",
    "import",
    "let",
    "var",
    "void",
    "with",
    "yield",
    "this",
    "new",
    "if",
    "for",
    "while",
}
_RISK_RANK = {"SAFE": 0, "REVIEW": 1, "DESTRUCTIVE": 2}
_SUPPORTED = {"navigate", "fill", "select", "click", "assert", "wait"}


@dataclass
class StateInput:
    state_code: str
    url_pattern: str
    elements: list[dict[str, Any]]


@dataclass
class CaseInput:
    test_case_id: UUID
    tc_code: str
    version: int
    title: str
    requirement_id: UUID
    requirement_version: int
    application_map_id: UUID
    application_map_version: int
    project_id: UUID
    steps: list[dict[str, Any]]
    test_data: dict[str, Any]
    expected_result: str
    credential_ref: str | None = None
    preconditions: list[str] = field(default_factory=list)
    category: str = ""
    objective: str = ""


@dataclass
class PlannedScript:
    test_case_id: UUID
    tc_code: str
    version: int
    title: str
    requirement_id: UUID
    requirement_version: int
    application_map_id: UUID
    application_map_version: int
    project_id: UUID
    spec_path: str
    risk: str
    blocked: bool
    blocked_reason: str | None
    selectors: list[dict[str, Any]]
    area: str


@dataclass
class SuitePlan:
    generation_id: UUID
    suite_dir: Path
    scripts: list[PlannedScript]
    selector_summary: dict[str, int]
    risk_level: str
    file_tree: list[str] = field(default_factory=list)


def _carry_local_env(suite_dir: Path) -> None:
    """Reuse the account file from the previous suite in this project folder.

    A new generation only writes .env.example. Login needs the local .env,
    and that file is never put in the download.
    """
    parent = suite_dir.parent
    if not parent.is_dir():
        return
    candidates = sorted(
        (path for path in parent.glob("*/.env") if path.parent != suite_dir),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?m)^TEST_USERNAME=\S", text) and re.search(r"(?m)^TEST_PASSWORD=\S", text):
            (suite_dir / ".env").write_text(text, encoding="utf-8")
            return


def case_ids_to_write(requested: list[tuple[UUID, int]], stored: dict[UUID, int]) -> list[UUID]:
    """Cases with no spec yet, or whose approved version changed."""
    return [case_id for case_id, version in requested if stored.get(case_id) != version]


def generate_suite(
    *,
    suite_dir: Path,
    generation_id: UUID,
    project_id: UUID,
    application_url: str | None,
    cases: list[CaseInput],
    states: dict[str, StateInput],
    incremental: bool = False,
) -> SuitePlan:
    if incremental:
        suite_dir.mkdir(parents=True, exist_ok=True)
    else:
        if suite_dir.exists():
            shutil.rmtree(suite_dir)
        suite_dir.mkdir(parents=True)

    forbidden = _forbidden_literals(cases)
    planned_cases = [_plan_case(case, states, forbidden, generation_id) for case in cases]
    pages = _pages_for(planned_cases, states)
    data_cases = _data_cases(planned_cases)
    env_names = [
        field["env"]
        for case in data_cases
        for field in case["fields"]
        if field["env"] not in {"TEST_USERNAME", "TEST_PASSWORD", "BASE_URL", "RUN_DESTRUCTIVE"}
    ]

    write = _write_if_absent if incremental else _write
    write(suite_dir / "package.json", _package_json(project_id))
    write(suite_dir / "tsconfig.json", _tsconfig())
    write(suite_dir / "env.d.ts", _ENV_DTS)
    write(
        suite_dir / "playwright.config.ts",
        templates.render_config(base_url=json.dumps(application_url) if application_url else None),
    )
    write(suite_dir / ".gitignore", templates.render_gitignore())
    write(
        suite_dir / ".env.example",
        templates.render_env_example(base_url=application_url or "", names=sorted(set(env_names))),
    )
    if not (suite_dir / ".env").is_file():
        _carry_local_env(suite_dir)
    write(
        suite_dir / "fixtures" / "auth.ts",
        templates.render_auth(base_url=json.dumps(application_url) if application_url else '""'),
    )
    _merge_testdata(suite_dir / "data" / "testdata.ts", data_cases, replace=not incremental)
    written_pages: list[Path] = []
    for page in pages.values():
        page_path = suite_dir / "pages" / page["file_name"]
        rendered_page = templates.render_page(**page)
        if incremental and page_path.is_file():
            merged = _augment_page(page_path.read_text(encoding="utf-8"), rendered_page)
            if merged != page_path.read_text(encoding="utf-8"):
                _write(page_path, merged)
                written_pages.append(page_path)
        else:
            _write(page_path, rendered_page)
            written_pages.append(page_path)
    written_specs: list[Path] = []
    for planned in planned_cases:
        spec_path = suite_dir / planned.spec_path
        _write(spec_path, planned.source)
        written_specs.append(spec_path)

    map_id = cases[0].application_map_id if cases else None
    map_version = cases[0].application_map_version if cases else None
    scripts = [_public_script(item) for item in planned_cases]
    manifest = {
        "framework": "playwright",
        "executed": False,
        "project_id": str(project_id),
        "generation_id": str(generation_id),
        "application_url": application_url,
        "application_map_id": str(map_id) if map_id else None,
        "application_map_version": map_version,
        "verification_status": "NOT_VERIFIED",
        "files": [],
        "cases": [
            {
                "test_case_id": str(item.test_case_id),
                "test_case_code": item.tc_code,
                "test_case_version": item.version,
                "requirement_id": str(item.requirement_id),
                "requirement_version": item.requirement_version,
                "application_map_id": str(item.application_map_id),
                "application_map_version": item.application_map_version,
                "spec_path": item.spec_path,
                "risk": item.risk,
                "status": "blocked" if item.blocked else "generated",
                "blocked_reason": item.blocked_reason,
            }
            for item in scripts
        ],
    }
    if incremental:
        manifest = _merge_manifest(suite_dir / "manifest.json", manifest)
    _write(suite_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    write(
        suite_dir / "README.md",
        templates.render_readme(
            project_id=project_id,
            generation_id=generation_id,
            map_id=map_id or "",
            map_version=map_version or "",
        ),
    )
    _reject_secret_literals(
        suite_dir,
        forbidden,
        only=written_specs + written_pages if incremental else None,
    )
    manifest["files"] = list_files(suite_dir)
    _write(suite_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")

    summary: dict[str, int] = {}
    for item in scripts:
        if item.blocked:
            continue
        for selector in item.selectors:
            strategy = str(selector.get("strategy") or "unresolved")
            summary[strategy] = summary.get(strategy, 0) + 1
    risks = [item.risk for item in scripts] or ["SAFE"]
    return SuitePlan(
        generation_id=generation_id,
        suite_dir=suite_dir,
        scripts=scripts,
        selector_summary=summary,
        risk_level=_max_risk(risks),
        file_tree=manifest["files"],
    )


@dataclass
class _Draft:
    case: CaseInput
    blocked_reasons: list[str]
    risk: str
    selectors: list[SelectorDecision]
    spec_path: str
    source: str
    area: str
    data_fields: list[dict[str, str]]
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)
    blocked_reason: str | None = None
    fixture: str = "sessionPage"
    starting_state: str = "AUTHENTICATED"

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_reasons)

    @property
    def test_case_id(self) -> UUID:
        return self.case.test_case_id

    @property
    def tc_code(self) -> str:
        return self.case.tc_code

    @property
    def version(self) -> int:
        return self.case.version

    @property
    def title(self) -> str:
        return self.case.title

    @property
    def requirement_id(self) -> UUID:
        return self.case.requirement_id

    @property
    def requirement_version(self) -> int:
        return self.case.requirement_version

    @property
    def application_map_id(self) -> UUID:
        return self.case.application_map_id

    @property
    def application_map_version(self) -> int:
        return self.case.application_map_version

    @property
    def project_id(self) -> UUID:
        return self.case.project_id


def _public_script(item: _Draft) -> PlannedScript:
    return PlannedScript(
        test_case_id=item.test_case_id,
        tc_code=item.tc_code,
        version=item.version,
        title=item.title,
        requirement_id=item.requirement_id,
        requirement_version=item.requirement_version,
        application_map_id=item.application_map_id,
        application_map_version=item.application_map_version,
        project_id=item.project_id,
        spec_path=item.spec_path,
        risk=item.risk,
        blocked=item.blocked,
        blocked_reason=item.blocked_reason,
        selectors=[_selector_record(selector) for selector in item.selectors],
        area=item.area,
    )


def _plan_case(
    case: CaseInput,
    states: dict[str, StateInput],
    forbidden: set[str],
    generation_id: UUID,
) -> _Draft:
    reasons: list[str] = []
    selectors: list[SelectorDecision] = []
    risks = ["SAFE"]
    calls: list[tuple[str, str, str | None]] = []
    data_fields: list[dict[str, str]] = []
    used_props: dict[tuple[str, str], str] = {}
    prepared = prepare_execution(case, states)
    case.steps = prepared.steps
    case.test_data = prepared.test_data
    if prepared.blocked_reason:
        reasons.append(prepared.blocked_reason)
    leaves = _string_leaves(case.test_data)
    if not case.steps:
        reasons.append(f"{case.tc_code} has no steps.")

    for index, step in enumerate(case.steps, start=1):
        action = str(step.get("action") or "")
        target = step.get("target") if isinstance(step.get("target"), dict) else {}
        state_code = str(target.get("state_code") or "")
        element_code = target.get("element_code")
        element_code = str(element_code) if element_code else None
        if action not in _SUPPORTED:
            reasons.append(f"Step {index}: action {action!r} is not supported.")
            continue
        state = states.get(state_code)
        if state is None:
            reasons.append(
                f"Step {index}: state {state_code or '(missing)'} is not on the case's application map."
            )
            continue
        if action == "navigate":
            calls.append((state_code, "open", None))
            continue
        if action == "wait" and not element_code:
            calls.append((state_code, "waitForReady", None))
            continue
        if action in {"click", "fill", "select"} and not element_code:
            reasons.append(f"Step {index}: {action} requires an observed element_code.")
            continue
        if action == "assert" and not element_code:
            if state.url_pattern:
                calls.append((state_code, "expectOnPage", None))
            else:
                reasons.append(f"Step {index}: assert has no observed element or URL.")
            continue
        decision = resolve_in_state(state_code, element_code, state.elements)
        risks.append(decision.risk)
        selectors.append(decision)
        if decision.status != "resolved" or not decision.expression:
            reasons.append(f"Step {index}: {decision.reason}")
            continue
        prop = used_props.setdefault((state_code, element_code or ""), _prop_name(decision))
        element_name = str(target.get("element_name") or decision.evidence.get("name") or "")
        if action in {"fill", "select"}:
            expression, field = _fill_expression(case, index, step, element_name, leaves, forbidden)
            if field:
                data_fields.append(field)
            verb = "select" if action == "select" else "fill"
            calls.append((state_code, f"{verb}{_pascal(prop)}", expression))
        elif action == "click":
            calls.append((state_code, f"click{_pascal(prop)}", None))
        elif action == "wait":
            calls.append((state_code, f"waitFor{_pascal(prop)}", None))
        else:
            expected = str(step.get("expected") or case.expected_result or "")
            if expected and not _contains_secret(expected, forbidden):
                calls.append((state_code, f"expect{_pascal(prop)}", json.dumps(expected)))
            else:
                calls.append((state_code, f"expect{_pascal(prop)}Visible", None))

    risk = _max_risk(risks)
    area = _area(case, states, risk, bool(reasons))
    spec_path = f"tests/{area}/{case.tc_code}.{_slug(case.title, 'case')}.spec.ts"
    draft = _Draft(
        case=case,
        blocked_reasons=reasons,
        risk=risk,
        selectors=selectors,
        spec_path=spec_path,
        source="",
        area=area,
        data_fields=data_fields,
        calls=[] if reasons else calls,
        blocked_reason="; ".join(reasons) if reasons else None,
        fixture=prepared.fixture,
        starting_state=prepared.starting_state,
    )
    draft.source = _render_case(draft, generation_id)
    return draft


def _render_case(draft: _Draft, generation_id: UUID) -> str:
    trace = {
        "project_id": draft.project_id,
        "requirement_id": draft.requirement_id,
        "requirement_version": draft.requirement_version,
        "test_case_id": draft.test_case_id,
        "test_case_code": draft.tc_code,
        "test_case_version": draft.version,
        "application_map_id": draft.application_map_id,
        "application_map_version": draft.application_map_version,
        "generation_id": generation_id,
    }
    reason = _comment(draft.blocked_reason or "Blocked.")
    if draft.blocked:
        return templates.render_blocked(
            trace=trace,
            reason=reason,
            title=json.dumps(f"{draft.tc_code} blocked"),
        )
    pages = []
    seen: set[str] = set()
    lines: list[str] = []
    for state_code, method, argument in draft.calls:
        var = _instance(state_code)
        class_name = _class_name(state_code)
        file_name = f"{_slug(state_code, 'state')}.page"
        if state_code not in seen:
            seen.add(state_code)
            pages.append({"class_name": class_name, "file_name": file_name})
            lines.append(f"const {var} = new {class_name}(page);")
            lines.append(f"{var}.expectedState = {json.dumps(draft.starting_state)};")
        if argument is None:
            lines.append(f"await {var}.{method}();")
        else:
            lines.append(f"await {var}.{method}({argument});")
    if not lines:
        lines.append("// No supported observed steps were available.")
    return templates.render_spec(
        pages=pages,
        uses_data=any("testData." in line for line in lines),
        trace=trace,
        destructive=draft.risk == "DESTRUCTIVE",
        title=json.dumps(f"{draft.tc_code} {draft.title}"),
        lines=lines,
        fixture=draft.fixture,
    )


def _pages_for(cases: list[_Draft], states: dict[str, StateInput]) -> dict[str, dict[str, Any]]:
    needed: dict[str, dict[str, Any]] = {}

    def page_for(state_code: str) -> dict[str, Any]:
        state = states.get(state_code)
        return needed.setdefault(
            state_code,
            {
                "state_code": state_code,
                "class_name": _class_name(state_code),
                "file_name": f"{_slug(state_code, 'state')}.page.ts",
                "url_literal": json.dumps(_observed_path(state.url_pattern if state else "/")),
                "url_pattern": _js_regex(_observed_path(state.url_pattern if state else "/")),
                "locators": [],
                "actions": [],
                "_props": {},
                "_actions": set(),
            },
        )

    for case in cases:
        if case.blocked:
            continue
        for selector in case.selectors:
            if selector.status != "resolved" or not selector.expression or not selector.state_code:
                continue
            page = page_for(selector.state_code)
            code = selector.element_code or ""
            if code not in page["_props"]:
                prop = _prop_name(selector)
                page["_props"][code] = prop
                page["locators"].append({"prop": prop, "expression": selector.expression})
        for state_code, method, _argument in case.calls:
            page = page_for(state_code)
            if method in {"open", "waitForReady", "expectOnPage"} or method in page["_actions"]:
                continue
            for prop in page["_props"].values():
                action = _action_for(method, prop)
                if action is None:
                    continue
                page["actions"].append(action)
                page["_actions"].add(method)
                break
    for page in needed.values():
        page.pop("_props", None)
        page.pop("_actions", None)
    return needed


def _action_for(method: str, prop: str) -> dict[str, str] | None:
    pascal = _pascal(prop)
    mapping = {
        f"fill{pascal}": (
            "value: string",
            f"await this.guard(async () => {{ await this.{prop}.fill(value); }});",
        ),
        f"select{pascal}": (
            "value: string",
            "await this.guard(async () => {"
            f" const tag = await this.{prop}.evaluate((node) => node.tagName).catch(() => \"\");"
            f" if (tag === \"SELECT\") {{ await this.{prop}.selectOption({{ label: value }}).catch(async () => {{ await this.{prop}.selectOption(value); }}); return; }}"
            f" await this.{prop}.click();"
            " await this.page.getByRole(\"option\", { name: value, exact: true }).click();"
            " });",
        ),
        f"click{pascal}": ("", f"await this.guard(async () => {{ await this.{prop}.click(); }});"),
        f"waitFor{pascal}": ("", f'await this.{prop}.waitFor({{ state: "visible" }});'),
        f"expect{pascal}": ("expected: string", f"await expect(this.{prop}).toContainText(expected);"),
        f"expect{pascal}Visible": ("", f"await expect(this.{prop}).toBeVisible();"),
    }
    if method not in mapping:
        return None
    params, body = mapping[method]
    return {"name": method, "params": params, "body": body}


def _data_cases(cases: list[_Draft]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for case in cases:
        if case.blocked:
            continue
        code = _case_key(case.tc_code)
        for item in case.data_fields:
            bucket = grouped.setdefault(code, [])
            if item not in bucket:
                bucket.append(item)
    return [{"code": code, "fields": fields} for code, fields in grouped.items()]


def _fill_expression(
    case: CaseInput,
    index: int,
    step: dict[str, Any],
    element_name: str,
    leaves: list[tuple[str, str]],
    forbidden: set[str],
) -> tuple[str, dict[str, str] | None]:
    raw = str(step.get("value") or "")
    placeholder = re.fullmatch(r"\{([^{}]+)\}", raw.strip())
    leaf_path = None
    source = raw
    if placeholder:
        key = placeholder.group(1)
        found = next((path for path, _value in leaves if path == key or path.endswith("." + key)), None)
        if found:
            leaf_path = found
            source = dict(leaves)[found]
        field_key = key
    else:
        field_key = f"step_{index}"
        found = next((path for path, value in leaves if value == raw and raw), None)
        if found:
            leaf_path = found
            field_key = found
    if source in {"wrong", "invalid-captcha", "not-an-email"} or (
        source == "" and (case.category or "").upper() == "NEGATIVE"
    ):
        return json.dumps(source), None
    sensitive = bool(
        _SENSITIVE.search(element_name)
        or _SENSITIVE.search(field_key)
        or _contains_secret(source, forbidden)
        or leaf_path
    )
    if not sensitive:
        return json.dumps(source), None
    env = _env_name(case.tc_code, field_key)
    name = _identifier(field_key.split(".")[-1], f"step{index}")
    return f"testData.{_case_key(case.tc_code)}.{name}", {"name": name, "env": env}


def _forbidden_literals(cases: list[CaseInput]) -> set[str]:
    forbidden: set[str] = set()
    for case in cases:
        if case.credential_ref and len(case.credential_ref) >= 4:
            forbidden.add(case.credential_ref)
        for path, value in _string_leaves(case.test_data):
            if len(value) >= 4 and _SENSITIVE.search(path):
                forbidden.add(value)
        for step in case.steps:
            target = step.get("target") if isinstance(step.get("target"), dict) else {}
            name = str(target.get("element_name") or "")
            value = step.get("value")
            if isinstance(value, str) and len(value) >= 4 and _SENSITIVE.search(name):
                forbidden.add(value)
    return {item for item in forbidden if item.strip()}


def _string_leaves(data: dict[str, Any]) -> list[tuple[str, str]]:
    leaves: list[tuple[str, str]] = []

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                walk(f"{prefix}.{key}" if prefix else str(key), item)
        elif isinstance(value, str):
            leaves.append((prefix, value))

    walk("", data or {})
    return leaves


def _contains_secret(text: str, forbidden: set[str]) -> bool:
    return any(secret and secret in text for secret in forbidden)


def _reject_secret_literals(
    suite_dir: Path, forbidden: set[str], *, only: list[Path] | None = None
) -> None:
    if not forbidden:
        return
    if only is None:
        paths = [path for path in suite_dir.rglob("*") if path.is_file()]
    else:
        paths = [path for path in only if path.is_file()]
    hits = []
    for path in paths:
        if any(part in _SECRET_SKIP for part in path.relative_to(suite_dir).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _contains_secret(text, forbidden):
            hits.append(path)
    if not hits:
        return
    if only is None:
        shutil.rmtree(suite_dir, ignore_errors=True)
    else:
        for path in hits:
            path.unlink(missing_ok=True)
    raise RuntimeError("Refusing to keep a suite that contained a secret literal.")


def _selector_record(selector: SelectorDecision) -> dict[str, Any]:
    return {
        "status": selector.status,
        "strategy": selector.strategy,
        "expression": selector.expression,
        "evidence": selector.evidence,
        "reason": selector.reason,
        "xpath_fallback": selector.xpath_fallback,
        "risk": selector.risk,
        "state_code": selector.state_code,
        "element_code": selector.element_code,
    }


def _area(case: CaseInput, states: dict[str, StateInput], risk: str, blocked: bool) -> str:
    if blocked:
        return "blocked"
    if risk == "DESTRUCTIVE":
        return "destructive"
    for step in case.steps:
        target = step.get("target") if isinstance(step.get("target"), dict) else {}
        state = states.get(str(target.get("state_code") or ""))
        if state and state.url_pattern:
            return _slug(_observed_path(state.url_pattern).strip("/").split("/")[0], "general")
    return "general"


def _observed_path(url_pattern: str) -> str:
    if not url_pattern:
        return "/"
    if "://" in url_pattern:
        parsed = urlparse(url_pattern)
        path = parsed.path or "/"
        if parsed.fragment:
            path += "#" + parsed.fragment
        return path
    return url_pattern if url_pattern.startswith("/") else f"/{url_pattern}"


def _js_regex(path: str) -> str:
    return f"new RegExp({json.dumps(re.escape(path))})"


def _slug(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (text[:48] or fallback).strip("-") or fallback


def _class_name(state_code: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", state_code) or ["State"]
    return "".join(part.capitalize() for part in parts) + "Page"


def _instance(state_code: str) -> str:
    return _identifier(state_code, "state")


def _case_key(tc_code: str) -> str:
    return _identifier(tc_code, "case")


def _pascal(prop: str) -> str:
    return prop[:1].upper() + prop[1:]


def _prop_name(selector: SelectorDecision) -> str:
    evidence = selector.evidence
    raw = str(evidence.get("name") or evidence.get("getByLabel") or "element")
    return _identifier(f"{raw}-{selector.element_code or 'element'}", "element")


def _identifier(value: str, fallback: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value)
    if not parts:
        parts = [fallback]
    name = parts[0].lower() + "".join(part.capitalize() for part in parts[1:])
    if not name[:1].isalpha():
        name = fallback + name[:1].upper() + name[1:]
    if name in _RESERVED:
        name += "Field"
    return name


def _env_name(*parts: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", "_".join(parts)).strip("_").upper()
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"FIELD_{cleaned}"
    return cleaned[:80]


def _comment(value: str) -> str:
    return value.replace("*/", "* /").replace("\n", " ")


def _max_risk(levels: list[str]) -> str:
    return max(levels, key=lambda item: _RISK_RANK.get(item, 0))


_SECRET_SKIP = {"node_modules", "test-results", "playwright-report", "blob-report"}
_METHOD_BLOCK = re.compile(r"\n  async (\w+)\([^)]*\) \{.*?\n  \}\n", re.S)
_LOCATOR_LINE = re.compile(r"^  readonly (?!page\b)(\w+): Locator;$", re.M)
_ASSIGN_LINE = re.compile(r"^    this\.(\w+) = .*;$", re.M)
_GUARD_METHOD = """
  async guard(action: () => Promise<void>): Promise<void> {
    try {
      await action();
    } catch (error) {
      const heading = await this.page.getByRole("heading").first().textContent().catch(() => "");
      const extra = `Expected starting state: ${this.expectedState}\\nCurrent URL: ${this.page.url()}\\nVisible heading: ${(heading || "").trim() || "(none)"}`;
      if (error instanceof Error) {
        error.message = `${error.message}\\n${extra}`;
        throw error;
      }
      throw error;
    }
  }
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_if_absent(path: Path, content: str) -> None:
    if path.is_file():
        return
    _write(path, content)


def _merge_testdata(path: Path, data_cases: list[dict[str, Any]], *, replace: bool) -> None:
    rendered = templates.render_testdata(cases=data_cases)
    if replace or not path.is_file():
        _write(path, rendered)
        return
    text = path.read_text(encoding="utf-8")
    missing = [
        case
        for case in data_cases
        if not re.search(rf"\b{re.escape(str(case['code']))}\s*:", text)
    ]
    if not missing:
        return
    if "} as const;" not in text or "export const testData = {" not in text:
        _write(path, rendered)
        return
    addition = templates.render_testdata(cases=missing)
    inner = addition.split("export const testData = {", 1)[1].rsplit("} as const;", 1)[0]
    _write(path, text.replace("} as const;", inner + "} as const;", 1))


def _augment_page(existing: str, rendered: str) -> str:
    """Add locators and methods the new spec needs. Leave a rewritten class alone."""
    if "export class" not in existing or "readonly page: Page;" not in existing:
        return existing
    updated = existing
    if "expectedState" not in updated:
        updated = updated.replace(
            "  readonly page: Page;\n",
            '  readonly page: Page;\n  expectedState = "AUTHENTICATED";\n',
            1,
        )
    for name in _LOCATOR_LINE.findall(rendered):
        if f"readonly {name}:" in updated:
            continue
        line = next(line for line in rendered.splitlines() if line.startswith(f"  readonly {name}:"))
        updated = updated.replace("  readonly page: Page;\n", f"  readonly page: Page;\n{line}\n", 1)
    for name in _ASSIGN_LINE.findall(rendered):
        if name == "page" or f"this.{name} =" in updated:
            continue
        line = next(line for line in rendered.splitlines() if line.startswith(f"    this.{name} ="))
        anchor = "    this.page = page;\n"
        if anchor not in updated:
            continue
        updated = updated.replace(anchor, anchor + line + "\n", 1)
    for match in _METHOD_BLOCK.finditer(rendered):
        name = match.group(1)
        if name in {"open", "waitForReady", "expectOnPage"}:
            continue
        if re.search(rf"\basync {name}\s*\(", updated):
            continue
        if not updated.rstrip().endswith("}"):
            continue
        updated = updated.rstrip()[:-1].rstrip() + "\n" + match.group(0).strip("\n") + "\n}\n"
    if "async guard(" not in updated and updated.rstrip().endswith("}"):
        updated = updated.rstrip()[:-1].rstrip() + "\n" + _GUARD_METHOD.strip("\n") + "\n}\n"
    return updated


def _merge_manifest(path: Path, fresh: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return fresh
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fresh
    if not isinstance(existing, dict):
        return fresh
    by_id = {
        str(item.get("test_case_id")): item
        for item in existing.get("cases") or []
        if isinstance(item, dict) and item.get("test_case_id")
    }
    for item in fresh.get("cases") or []:
        by_id[str(item["test_case_id"])] = item
    existing["cases"] = list(by_id.values())
    existing["project_id"] = fresh["project_id"]
    existing["generation_id"] = fresh["generation_id"]
    existing["application_url"] = fresh["application_url"]
    return existing


def _package_json(project_id: UUID) -> str:
    return json.dumps(
        {
            "name": f"playwright-suite-{str(project_id)[:8]}",
            "private": True,
            "scripts": {
                "test:list": "playwright test --list",
                "typecheck": "tsc --noEmit",
            },
            "devDependencies": {
                "@playwright/test": "^1.56.0",
                "typescript": "~5.9.3",
            },
        },
        indent=2,
    ) + "\n"


def _tsconfig() -> str:
    return json.dumps(
        {
            "compilerOptions": {
                "target": "ES2022",
                "module": "commonjs",
                "moduleResolution": "node",
                "strict": True,
                "esModuleInterop": True,
                "skipLibCheck": True,
                "noEmit": True,
                "types": [],
            },
            "include": ["./**/*.ts"],
        },
        indent=2,
    ) + "\n"


_ENV_DTS = """\
declare const process: {
  env: Record<string, string | undefined>;
};
"""


def update_manifest_verification(suite_dir: Path, status: str) -> None:
    path = suite_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["verification_status"] = status
    manifest["files"] = list_files(suite_dir)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
