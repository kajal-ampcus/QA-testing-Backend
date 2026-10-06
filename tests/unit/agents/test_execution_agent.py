import uuid
from pathlib import Path

import pytest

from core.agents.test_execution.agent import TestExecutionAgent
from core.agents.test_execution.playwright_runner import RawTestOutcome, SuiteRunResult
from domain.enums import (
    AutomationReviewStatus,
    EvidenceSource,
    RiskLevel,
    TestResultStatus,
    TestRunStatus,
)
from infra.db.models.automation import AutomationScript
from infra.db.models.execution import TestRun
from infra.object_storage.s3_client import InMemoryS3Client
from schemas.envelope import AgentInputEnvelope


class _EmptyScalars:
    def all(self) -> list[object]:
        return []


class _EmptyResult:
    def scalars(self) -> _EmptyScalars:
        return _EmptyScalars()


class _VersionResult:
    def __init__(self, versions: list[object]) -> None:
        self.versions = versions

    def scalars(self) -> "_VersionResult":
        return self

    def all(self) -> list[object]:
        return self.versions


class FakeSession:
    def __init__(self, versions: list[object] | None = None) -> None:
        self.versions = versions or []

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, obj: object, attribute_names: list[str] | None = None) -> None:
        if attribute_names is None and hasattr(obj, "results"):
            obj.results = []

    async def execute(self, _statement: object) -> _EmptyResult:
        return _VersionResult(self.versions)


class FakeExecutionRepo:
    def __init__(self, run: TestRun, versions: list[object] | None = None) -> None:
        self.run = run
        self.session = FakeSession(versions)

    async def get_run(self, run_id: uuid.UUID) -> TestRun | None:
        return self.run if self.run.id == run_id else None

    def mark_running(self, run: TestRun) -> None:
        run.status = TestRunStatus.RUNNING

    def mark_finished(self, run: TestRun, status: str, summary: dict[str, int]) -> None:
        run.status = status
        run.summary = summary

    def add_result(self, result: object) -> object:
        return result


class FakeAutomationRepo:
    def __init__(self, scripts: list[AutomationScript]) -> None:
        self.scripts = scripts

    async def list_generation(self, _project_id: uuid.UUID, _generation_id: uuid.UUID) -> list[AutomationScript]:
        return self.scripts


class FakeRunner:
    def __init__(self, result: SuiteRunResult) -> None:
        self.result = result

    async def run_suite(self, *_args: object, **_kwargs: object) -> SuiteRunResult:
        return self.result


def _script(project_id: uuid.UUID, generation_id: uuid.UUID, **overrides: object) -> AutomationScript:
    values: dict[str, object] = dict(
        id=uuid.uuid4(),
        script_code="AUTO-001",
        project_id=project_id,
        generation_id=generation_id,
        test_case_id=uuid.uuid4(),
        test_case_code="TC-001",
        test_case_version=1,
        application_map_id=uuid.uuid4(),
        application_map_version=1,
        framework="playwright",
        file_path="tests/login.spec.ts",
        selector_strategy=[],
        risk_level=RiskLevel.SAFE,
        review_status=AutomationReviewStatus.REVIEWED,
        review_findings={},
        suite_summary={},
    )
    values.update(overrides)
    return AutomationScript(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_execution_agent_persists_results_without_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _quiet(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("core.agents.test_execution.agent.claim_live_display", _quiet)
    monkeypatch.setattr("core.agents.test_execution.agent.release_live_display", _quiet)
    project_id = uuid.uuid4()
    generation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    script = _script(project_id, generation_id)
    run = TestRun(
        id=run_id,
        project_id=project_id,
        generation_id=generation_id,
        environment="development",
        status=TestRunStatus.QUEUED,
        summary={},
    )
    run.results = []
    s3 = InMemoryS3Client()
    outcome = RawTestOutcome(
        spec_path=script.file_path,
        title="logs in",
        status=TestResultStatus.PASSED,
        duration_ms=15,
        error_message=None,
        expected="pass",
        actual="pass",
        screenshot=b"png",
        video=b"webm",
        trace=b"zip",
        console_log=b"[]",
        network_log=b"[]",
        script_id=script.id,
    )
    agent = TestExecutionAgent(
        execution_repo=FakeExecutionRepo(run),  # type: ignore[arg-type]
        automation_repo=FakeAutomationRepo([script]),  # type: ignore[arg-type]
        s3=s3,
        runner=FakeRunner(SuiteRunResult(process_ok=True, detail="", outcomes=[outcome])),  # type: ignore[arg-type]
    )
    envelope = await agent.run(
        AgentInputEnvelope(
            agent_run_id=uuid.uuid4(),
            project_id=project_id,
            trigger="manual",
            payload={
                "run_id": str(run_id),
                "generation_id": str(generation_id),
                "suite_dir": str(tmp_path),
                "run_destructive": False,
                "base_url": "https://app.example.test",
            },
        )
    )
    assert envelope.token_usage is None
    assert run.status == TestRunStatus.COMPLETED
    assert run.summary == {"passed": 1, "failed": 0, "skipped": 0, "error": 0}
    assert len(run.results) == 1
    stored = run.results[0]
    assert stored.status == TestResultStatus.PASSED
    assert stored.assertion["source"] == EvidenceSource.TEST_EXECUTION
    assert stored.assertion["expected"] == stored.assertion["actual"]
    assert all(stored.evidence[channel] for channel in ("screenshot", "video", "trace", "console_log", "network_log"))
    assert script.suite_summary.get("executed") is True
    assert envelope.token_usage is None
    keys = list(s3.objects)
    assert len(keys) == 5


class _CaseVersion:
    def __init__(self, script: AutomationScript) -> None:
        self.test_case_id = script.test_case_id
        self.version = script.test_case_version
        self.title = "Sign in shows the dashboard"
        self.category = "POSITIVE"
        self.steps = [
            {
                "action": "fill",
                "target": {"element_name": "Email"},
                "value": "user@example.com",
            }
        ]
        self.test_data: dict[str, object] = {}


@pytest.mark.asyncio
async def test_failed_result_carries_the_case_title_and_a_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _quiet(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("core.agents.test_execution.agent.claim_live_display", _quiet)
    monkeypatch.setattr("core.agents.test_execution.agent.release_live_display", _quiet)
    project_id = uuid.uuid4()
    generation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    script = _script(project_id, generation_id)
    run = TestRun(
        id=run_id,
        project_id=project_id,
        generation_id=generation_id,
        environment="development",
        status=TestRunStatus.QUEUED,
        summary={},
    )
    run.results = []
    outcome = RawTestOutcome(
        spec_path=script.file_path,
        title="logs in",
        status=TestResultStatus.FAILED,
        duration_ms=40,
        error_message="locator was not found",
        expected="Dashboard is visible",
        actual="login page",
        screenshot=None,
        video=None,
        trace=None,
        console_log=None,
        network_log=None,
        script_id=script.id,
    )
    agent = TestExecutionAgent(
        execution_repo=FakeExecutionRepo(run, [_CaseVersion(script)]),  # type: ignore[arg-type]
        automation_repo=FakeAutomationRepo([script]),  # type: ignore[arg-type]
        s3=InMemoryS3Client(),
        runner=FakeRunner(SuiteRunResult(process_ok=True, detail="", outcomes=[outcome])),  # type: ignore[arg-type]
    )
    await agent.run(
        AgentInputEnvelope(
            agent_run_id=uuid.uuid4(),
            project_id=project_id,
            trigger="manual",
            payload={
                "run_id": str(run_id),
                "generation_id": str(generation_id),
                "suite_dir": str(tmp_path),
                "run_destructive": False,
            },
        )
    )
    stored = run.results[0]
    assert stored.assertion["title"] == "Sign in shows the dashboard"
    assert stored.assertion["category"] == "POSITIVE"
    assert stored.assertion["inputs"] == [{"name": "Email", "value": "user@example.com"}]
    assert stored.assertion["cause"] == "Playwright could not find the element."
    assert run.status == TestRunStatus.FAILED
