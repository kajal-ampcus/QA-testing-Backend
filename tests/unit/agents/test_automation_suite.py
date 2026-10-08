import json
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from core.agents.automation_generation.artifacts import build_zip, ide_links
from core.agents.automation_generation.suite import (
    CaseInput,
    StateInput,
    case_ids_to_write,
    generate_suite,
)
from core.agents.automation_review.lint_rules import lint_suite


def _ids():
    return {
        "project_id": uuid4(),
        "requirement_id": uuid4(),
        "case_id": uuid4(),
        "map_id": uuid4(),
    }


def _case(ids, **overrides) -> CaseInput:
    data = dict(
        test_case_id=ids["case_id"],
        tc_code="TC-001",
        version=2,
        title="Sign in",
        requirement_id=ids["requirement_id"],
        requirement_version=4,
        application_map_id=ids["map_id"],
        application_map_version=3,
        project_id=ids["project_id"],
        steps=[
            {"step_number": 1, "action": "navigate", "target": {"state_code": "STATE-001"}},
            {
                "step_number": 2,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-001", "element_name": "Email"},
                "value": "{email}",
            },
            {
                "step_number": 3,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-002", "element_name": "Password"},
                "value": "{password}",
            },
            {
                "step_number": 4,
                "action": "click",
                "target": {"state_code": "STATE-001", "element_code": "EL-003", "element_name": "Sign in"},
            },
            {
                "step_number": 5,
                "action": "assert",
                "target": {"state_code": "STATE-002", "element_code": "EL-001", "element_name": "Welcome"},
                "expected": "Welcome back",
            },
        ],
        test_data={"email": "user@example.com", "password": "Sup3rSecret!"},
        expected_result="Welcome back",
        credential_ref="cred:should-not-leak",
    )
    data.update(overrides)
    return CaseInput(**data)


def _states() -> dict[str, StateInput]:
    return {
        "STATE-001": StateInput(
            state_code="STATE-001",
            url_pattern="https://shop.example/login",
            elements=[
                {"element_code": "EL-001", "role": "textbox", "name": "Email", "risk": "SAFE", "source": "OBSERVED_DOM"},
                {
                    "element_code": "EL-002",
                    "role": "textbox",
                    "name": "Password",
                    "risk": "SAFE",
                    "source": "OBSERVED_DOM",
                },
                {"element_code": "EL-003", "role": "button", "name": "Sign in", "risk": "SAFE", "source": "OBSERVED_DOM"},
            ],
        ),
        "STATE-002": StateInput(
            state_code="STATE-002",
            url_pattern="https://shop.example/home",
            elements=[
                {"element_code": "EL-001", "role": "heading", "name": "Welcome", "risk": "SAFE", "source": "OBSERVED_DOM"}
            ],
        ),
    }


def test_suite_contains_pom_files_and_keeps_traceability(tmp_path) -> None:
    ids = _ids()
    plan = generate_suite(
        suite_dir=tmp_path / "suite",
        generation_id=uuid4(),
        project_id=ids["project_id"],
        application_url="https://shop.example",
        cases=[_case(ids)],
        states=_states(),
    )
    root = plan.suite_dir
    for name in ("package.json", "playwright.config.ts", "tsconfig.json", ".gitignore", ".env.example", "README.md", "manifest.json", "fixtures/auth.ts", "data/testdata.ts"):
        assert (root / name).is_file(), name
    assert (root / "pages" / "state-001.page.ts").is_file()
    assert (root / "pages" / "state-002.page.ts").is_file()
    specs = list((root / "tests").rglob("*.spec.ts"))
    assert specs and specs[0].name.startswith("TC-001.")
    spec = specs[0].read_text(encoding="utf-8")
    assert f"test_case_id: {ids['case_id']}" in spec
    assert "test_case_version: 2" in spec
    assert f"application_map_id: {ids['map_id']}" in spec
    assert "application_map_version: 3" in spec
    assert 'getByRole("heading", { name: "Welcome", exact: true })' in (root / "pages" / "state-002.page.ts").read_text(encoding="utf-8")
    assert "await " in spec
    assert 'from "../../fixtures/auth"' in spec
    auth = (root / "fixtures" / "auth.ts").read_text(encoding="utf-8")
    assert 'img[alt="CAPTCHA"]' not in auth
    assert 'img[alt*="captcha" i]' in auth
    assert "core.agents.test_execution.captcha_solve" in auth
    assert 'querySelectorAll("tspan")' in auth
    assert 'querySelectorAll("text, tspan")' not in auth
    assert r"(\d{1,2})" in auth
    assert "captcha|answer" in auth
    assert "publicPage: page" in spec
    assert "sessionPage: page" not in spec
    assert "test.beforeEach" not in auth
    assert "openContext(browser, false)" in auth
    assert "openContext(browser, true)" in auth
    config = (root / "playwright.config.ts").read_text(encoding="utf-8")
    assert "workers: 1" in config
    assert "headless: false" in config
    assert "process.env[match[1]]?.trim()" in config
    assert "expect(true)" not in spec
    assert "waitForTimeout" not in spec
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cases"][0]["test_case_code"] == "TC-001"
    assert manifest["executed"] is False
    blob = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file())
    assert "Sup3rSecret!" not in blob
    assert "cred:should-not-leak" not in blob
    assert "user@example.com" not in blob
    report = lint_suite(root, forbidden_literals=["Sup3rSecret!", "cred:should-not-leak"])
    assert report.missing_await == 0
    assert report.hardcoded_sleep == 0
    assert report.literal_credentials == 0
    assert report.missing_traceability == 0
    assert report.test_only == 0


def test_destructive_spec_is_skipped_by_default(tmp_path) -> None:
    ids = _ids()
    case = _case(
        ids,
        title="Delete account",
        steps=[
            {"step_number": 1, "action": "navigate", "target": {"state_code": "STATE-001"}},
            {
                "step_number": 2,
                "action": "click",
                "target": {"state_code": "STATE-001", "element_code": "EL-009", "element_name": "Delete account"},
            },
        ],
        test_data={},
        credential_ref=None,
    )
    states = {
        "STATE-001": StateInput(
            state_code="STATE-001",
            url_pattern="/account",
            elements=[
                {
                    "element_code": "EL-009",
                    "role": "button",
                    "name": "Delete account",
                    "risk": "DESTRUCTIVE",
                    "source": "OBSERVED_DOM",
                }
            ],
        )
    }
    plan = generate_suite(
        suite_dir=tmp_path / "suite",
        generation_id=uuid4(),
        project_id=ids["project_id"],
        application_url="https://shop.example",
        cases=[case],
        states=states,
    )
    spec = next((tmp_path / "suite").rglob("*.spec.ts"))
    assert "tests/destructive/" in spec.relative_to(plan.suite_dir).as_posix()
    text = spec.read_text(encoding="utf-8")
    assert 'test.skip(process.env.RUN_DESTRUCTIVE !== "true"' in text
    assert "sessionPage: page" in text
    assert plan.risk_level == "DESTRUCTIVE"
    report = lint_suite(plan.suite_dir)
    assert report.destructive_not_skipped == 0
    assert report.hardcoded_sleep == 0


def test_missing_locator_blocks_the_case_without_a_passing_assertion(tmp_path) -> None:
    ids = _ids()
    case = _case(
        ids,
        steps=[
            {
                "step_number": 1,
                "action": "click",
                "target": {"state_code": "STATE-001", "element_code": "EL-404"},
            }
        ],
        test_data={},
        credential_ref=None,
    )
    plan = generate_suite(
        suite_dir=tmp_path / "suite",
        generation_id=uuid4(),
        project_id=ids["project_id"],
        application_url="https://shop.example",
        cases=[case],
        states=_states(),
    )
    assert plan.scripts[0].blocked is True
    spec = next((tmp_path / "suite" / "tests" / "blocked").glob("*.spec.ts"))
    text = spec.read_text(encoding="utf-8")
    assert "test.fixme" in text
    assert "expect(true)" not in text
    assert "getByRole" not in text
    assert "EL-404" in (plan.scripts[0].blocked_reason or "")


def test_zip_contains_the_suite_and_skips_installed_dependencies(tmp_path) -> None:
    ids = _ids()
    plan = generate_suite(
        suite_dir=tmp_path / "suite",
        generation_id=uuid4(),
        project_id=ids["project_id"],
        application_url="https://shop.example",
        cases=[_case(ids)],
        states=_states(),
    )
    installed = plan.suite_dir / "node_modules" / "left-pad" / "index.js"
    installed.parent.mkdir(parents=True)
    installed.write_text("module.exports = 1;\n", encoding="utf-8")
    payload = build_zip(tmp_path, plan.suite_dir)
    names = zipfile.ZipFile(BytesIO(payload)).namelist()
    assert "playwright.config.ts" in names
    assert "README.md" in names
    assert "manifest.json" in names
    assert any(name.startswith("pages/") and name.endswith(".page.ts") for name in names)
    assert any(name.startswith("tests/") and name.endswith(".spec.ts") for name in names)
    assert not any(name.startswith("node_modules/") for name in names)


def test_ide_links_require_an_absolute_host_root() -> None:
    project_id = uuid4()
    generation_id = uuid4()
    assert ide_links("", project_id, generation_id) is None
    assert ide_links("artifacts/automation", project_id, generation_id) is None
    assert ide_links(r"D:\..\Windows", project_id, generation_id) is None
    host = str(Path(Path.cwd().anchor) / "qa" / "artifacts" / "automation")
    links = ide_links(host, project_id, generation_id)
    assert links is not None
    assert links["vscode"].startswith("vscode://file/")
    assert links["cursor"].startswith("cursor://file/")
    assert str(generation_id) in links["vscode"]
    windows = ide_links(r"D:\qa\artifacts\automation", project_id, generation_id)
    assert windows is not None
    assert windows["vscode"] == (
        f"vscode://file/D:/qa/artifacts/automation/{project_id}/{generation_id}"
    )
    assert windows["cursor"] == (
        f"cursor://file/D:/qa/artifacts/automation/{project_id}/{generation_id}"
    )


def test_later_cases_are_added_to_the_same_suite(tmp_path) -> None:
    ids = _ids()
    suite = tmp_path / "suite"
    generation_id = uuid4()
    shared = dict(
        suite_dir=suite,
        generation_id=generation_id,
        project_id=ids["project_id"],
        application_url="https://shop.example",
        states=_states(),
        incremental=True,
    )
    first = generate_suite(cases=[_case(ids)], **shared)
    spec = next(suite.rglob("TC-001*.spec.ts"))
    spec.write_text(spec.read_text(encoding="utf-8") + "\n// kept\n", encoding="utf-8")
    (suite / "fixtures" / "auth.ts").write_text("// edited in the editor\n", encoding="utf-8")
    second_ids = {**ids, "case_id": uuid4()}
    second = generate_suite(
        cases=[_case(second_ids, tc_code="TC-002", title="Stay signed in")],
        **shared,
    )
    assert first.generation_id == second.generation_id
    assert "// kept" in spec.read_text(encoding="utf-8")
    assert (suite / "fixtures" / "auth.ts").read_text(encoding="utf-8") == "// edited in the editor\n"
    assert any(path.name.startswith("TC-002") for path in suite.rglob("*.spec.ts"))
    manifest = json.loads((suite / "manifest.json").read_text(encoding="utf-8"))
    codes = {item["test_case_code"] for item in manifest["cases"]}
    assert codes == {"TC-001", "TC-002"}


def test_case_ids_to_write_skips_an_unchanged_version() -> None:
    kept = uuid4()
    changed = uuid4()
    added = uuid4()
    assert case_ids_to_write(
        [(kept, 1), (changed, 2), (added, 1)],
        {kept: 1, changed: 1},
    ) == [changed, added]
