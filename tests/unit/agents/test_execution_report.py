import uuid

from domain.enums import TestResultStatus, TestRunStatus
from core.agents.reporting.execution_report import (
    assemble_execution_report,
    run_is_finished,
)
from infra.db.models.automation import AutomationScript
from infra.db.models.execution import TestResult, TestRun
from infra.db.models.requirement import Requirement
from infra.db.models.test_case import TestCaseVersion


def test_unfinished_runs_have_no_report() -> None:
    assert run_is_finished(TestRunStatus.QUEUED) is False
    assert run_is_finished(TestRunStatus.RUNNING) is False
    assert run_is_finished(TestRunStatus.COMPLETED) is True
    assert run_is_finished(TestRunStatus.FAILED) is True


def test_report_joins_playwright_results_and_lists_failures_first() -> None:
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    generation_id = uuid.uuid4()
    passed_case = uuid.uuid4()
    failed_case = uuid.uuid4()
    requirement_id = uuid.uuid4()
    passed_script = uuid.uuid4()
    failed_script = uuid.uuid4()
    map_id = uuid.uuid4()
    run = TestRun(
        id=run_id,
        project_id=project_id,
        generation_id=generation_id,
        environment="development",
        base_url="https://app.example.test",
        status=TestRunStatus.FAILED,
        summary={"passed": 1, "failed": 1, "skipped": 0, "error": 0},
    )
    run.results = [
        TestResult(
            id=uuid.uuid4(),
            test_run_id=run_id,
            automation_script_id=passed_script,
            spec_path="tests/home.spec.ts",
            status=TestResultStatus.PASSED,
            assertion={"expected": "pass", "actual": "pass"},
            evidence={"screenshot": None, "video": "runs/video"},
            duration_ms=1200,
        ),
        TestResult(
            id=uuid.uuid4(),
            test_run_id=run_id,
            automation_script_id=failed_script,
            spec_path="tests/login.spec.ts",
            status=TestResultStatus.FAILED,
            assertion={"expected": "pass", "actual": "login stayed on the form"},
            evidence={"screenshot": "runs/shot"},
            duration_ms=800,
            error_message="Expected dashboard",
        ),
    ]
    scripts = [
        _script(passed_script, project_id, generation_id, passed_case, "TC-002", requirement_id, map_id),
        _script(failed_script, project_id, generation_id, failed_case, "TC-001", requirement_id, map_id),
    ]
    versions = [
        _version(passed_case, "Home is visible", "The home page is shown."),
        _version(failed_case, "Sign in", "Dashboard is visible after sign-in."),
    ]
    requirements = [
        Requirement(
            id=requirement_id,
            req_code="REQ-001",
            project_id=project_id,
            current_version=1,
            status="APPROVED",
        )
    ]

    report = assemble_execution_report(run, scripts, versions, requirements)

    assert report.counts.total == 2
    assert report.counts.passed == 1
    assert report.counts.failed == 1
    assert report.counts.pass_rate == 50.0
    assert report.counts.duration_ms == 2000
    assert [row.test_case_code for row in report.results] == ["TC-001", "TC-002"]
    failed = report.results[0]
    assert failed.title == "Sign in"
    assert failed.requirement_code == "REQ-001"
    assert failed.expected == "Dashboard is visible after sign-in."
    assert failed.actual == "login stayed on the form"
    assert failed.evidence == ["screenshot"]
    assert report.results[1].evidence == ["video"]


def _script(
    script_id: uuid.UUID,
    project_id: uuid.UUID,
    generation_id: uuid.UUID,
    case_id: uuid.UUID,
    code: str,
    requirement_id: uuid.UUID,
    map_id: uuid.UUID,
) -> AutomationScript:
    return AutomationScript(
        id=script_id,
        script_code=code.replace("TC", "AUTO"),
        project_id=project_id,
        generation_id=generation_id,
        test_case_id=case_id,
        test_case_code=code,
        test_case_version=1,
        requirement_id=requirement_id,
        requirement_version=1,
        application_map_id=map_id,
        application_map_version=1,
        framework="playwright",
        file_path=f"tests/{code}.spec.ts",
        selector_strategy=[],
        risk_level="SAFE",
        review_status="REVIEWED",
        review_findings={},
        suite_summary={},
    )


def _version(case_id: uuid.UUID, title: str, expected: str) -> TestCaseVersion:
    return TestCaseVersion(
        id=uuid.uuid4(),
        test_case_id=case_id,
        version=1,
        title=title,
        objective=title,
        preconditions=[],
        steps=[],
        expected_result=expected,
        test_data={},
        traceability=["AC-1"],
        category="POSITIVE",
        confidence=0.8,
    )
