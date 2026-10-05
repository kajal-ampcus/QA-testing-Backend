import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routers.v1 import executions
from domain.enums import AutomationReviewStatus, RiskLevel, TestRunStatus
from infra.db.models.automation import AutomationScript
from infra.db.models.execution import TestRun
from infra.db.models.project import Project


class FakeSession:
    def __init__(self, project: Project | None, run: TestRun | None = None) -> None:
        self.project = project
        self.run = run
        self.committed = False

    async def get(self, model: type, item_id: uuid.UUID) -> Any:
        if model is Project:
            return self.project
        if model is TestRun:
            return self.run
        return None

    async def commit(self) -> None:
        self.committed = True


class FakeAutomationRepo:
    def __init__(self, scripts: list[AutomationScript]) -> None:
        self.scripts = scripts

    async def list_generation(self, _project_id: uuid.UUID, _generation_id: uuid.UUID) -> list[AutomationScript]:
        return self.scripts


class FakeExecutionRepo:
    def __init__(self, active: TestRun | None = None, runs: list[TestRun] | None = None) -> None:
        self.active = active
        self.runs = list(runs or [])
        self.added: list[TestRun] = []

    async def active_for_generation(self, _project_id: uuid.UUID, _generation_id: uuid.UUID) -> TestRun | None:
        return self.active

    async def list_for_project(self, project_id: uuid.UUID) -> list[TestRun]:
        return [run for run in self.runs if run.project_id == project_id]

    def mark_finished(self, run: TestRun, status: str, summary: dict[str, object]) -> None:
        run.status = status
        run.summary = summary

    def add_run(self, run: TestRun) -> TestRun:
        self.added.append(run)
        return run


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


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []

    async def fake_enqueue(function_name: str, project_id: str, payload: dict[str, Any], job_id: str | None = None) -> str:
        payloads.append({"function": function_name, "project_id": project_id, "payload": payload, "job_id": job_id})
        return job_id or "job-1"

    monkeypatch.setattr(executions, "enqueue", fake_enqueue)
    monkeypatch.setattr(executions, "artifact_root", lambda: tmp_path)
    monkeypatch.setattr(executions, "generation_dir", lambda _root, _project, generation: tmp_path / str(generation))
    return payloads


@pytest.mark.asyncio
async def test_trigger_execution_enqueues_run_execution(
    queued: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = uuid.uuid4()
    generation_id = uuid.uuid4()
    (tmp_path / str(generation_id)).mkdir()
    project = Project(id=project_id, name="App", application_url="https://app.example.test", credential_ref="cred:qa")
    script = _script(project_id, generation_id)
    fake_exec = FakeExecutionRepo()
    monkeypatch.setattr(executions, "AutomationRepository", lambda _session: FakeAutomationRepo([script]))
    monkeypatch.setattr(executions, "ExecutionRepository", lambda _session: fake_exec)

    response = await executions.trigger_execution(
        project_id,
        executions.TriggerExecutionRequest(generation_id=generation_id),
        cast(AsyncSession, FakeSession(project)),
    )

    assert response.status == TestRunStatus.QUEUED
    assert queued[0]["function"] == "run_execution"
    assert queued[0]["payload"]["generation_id"] == str(generation_id)
    assert queued[0]["payload"]["base_url"] == "https://app.example.test"
    assert queued[0]["payload"]["credential_ref"] == "cred:qa"
    assert fake_exec.added[0].generation_id == generation_id


@pytest.mark.asyncio
async def test_trigger_execution_rejects_when_nothing_runnable(
    queued: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = uuid.uuid4()
    generation_id = uuid.uuid4()
    (tmp_path / str(generation_id)).mkdir()
    project = Project(id=project_id, name="App", application_url="https://app.example.test")
    script = _script(
        project_id,
        generation_id,
        risk_level=RiskLevel.DESTRUCTIVE,
        review_status=AutomationReviewStatus.PENDING_APPROVAL,
    )
    monkeypatch.setattr(executions, "AutomationRepository", lambda _session: FakeAutomationRepo([script]))
    monkeypatch.setattr(executions, "ExecutionRepository", lambda _session: FakeExecutionRepo())

    with pytest.raises(HTTPException) as error:
        await executions.trigger_execution(
            project_id,
            executions.TriggerExecutionRequest(generation_id=generation_id),
            cast(AsyncSession, FakeSession(project)),
        )
    assert error.value.status_code == 409
    assert queued == []


@pytest.mark.asyncio
async def test_trigger_execution_rejects_active_run(
    queued: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = uuid.uuid4()
    generation_id = uuid.uuid4()
    (tmp_path / str(generation_id)).mkdir()
    project = Project(id=project_id, name="App", application_url="https://app.example.test")
    script = _script(project_id, generation_id)
    active = TestRun(
        id=uuid.uuid4(),
        project_id=project_id,
        generation_id=generation_id,
        status=TestRunStatus.RUNNING,
        environment="development",
    )
    monkeypatch.setattr(executions, "AutomationRepository", lambda _session: FakeAutomationRepo([script]))
    monkeypatch.setattr(executions, "ExecutionRepository", lambda _session: FakeExecutionRepo(active=active))

    with pytest.raises(HTTPException) as error:
        await executions.trigger_execution(
            project_id,
            executions.TriggerExecutionRequest(generation_id=generation_id),
            cast(AsyncSession, FakeSession(project)),
        )
    assert error.value.status_code == 409
    assert queued == []


@pytest.mark.asyncio
async def test_stop_execution_cancels_the_projects_active_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid.uuid4()
    run = TestRun(
        id=uuid.uuid4(),
        project_id=project_id,
        generation_id=uuid.uuid4(),
        job_id="job-cep",
        status=TestRunStatus.RUNNING,
        environment="development",
        summary={"passed": 1, "failed": 0, "skipped": 0, "error": 0},
    )
    project = Project(id=project_id, name="CEP", application_url="https://cep.example.test")
    fake = FakeExecutionRepo(runs=[run])
    monkeypatch.setattr(executions, "ExecutionRepository", lambda _session: fake)

    class FakeJob:
        def __init__(self, job_id: str, _pool: object) -> None:
            self.job_id = job_id

        async def abort(self, timeout: int) -> bool:
            assert timeout == 5
            assert self.job_id == "job-cep"
            return True

    async def pool() -> object:
        return object()

    async def release(_run_id: str) -> None:
        return None

    monkeypatch.setattr(executions, "Job", FakeJob)
    monkeypatch.setattr(executions, "get_arq_pool", pool)
    monkeypatch.setattr(executions, "release_live_display", release)

    result = await executions.stop_execution(project_id, cast(AsyncSession, FakeSession(project)))

    assert result.status == "cancelled"
    assert result.job_id == "job-cep"
    assert run.status == TestRunStatus.CANCELLED
    assert run.summary["detail"] == "Execution was stopped."


@pytest.mark.asyncio
async def test_stop_execution_does_not_touch_a_finished_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid.uuid4()
    run = TestRun(
        id=uuid.uuid4(),
        project_id=project_id,
        generation_id=uuid.uuid4(),
        job_id="job-done",
        status=TestRunStatus.FAILED,
        environment="development",
        summary={"passed": 0, "failed": 1, "skipped": 0, "error": 0},
    )
    project = Project(id=project_id, name="Cafinity", application_url="https://cafinity.example.test")
    monkeypatch.setattr(
        executions, "ExecutionRepository", lambda _session: FakeExecutionRepo(runs=[run])
    )

    result = await executions.stop_execution(project_id, cast(AsyncSession, FakeSession(project)))

    assert result.status == "already_finished"
    assert run.status == TestRunStatus.FAILED


def test_summary_accepts_playwright_log_string() -> None:
    run = TestRun(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        generation_id=uuid.uuid4(),
        status=TestRunStatus.COMPLETED,
        environment="development",
        run_destructive=False,
        summary={
            "passed": 1,
            "failed": 0,
            "skipped": 0,
            "error": 0,
            "log": "Running 3 tests using 1 worker\n  1 passed",
            "detail": "ok",
        },
    )
    run.results = []
    payload = executions._summary(run)
    assert payload.summary == {"passed": 1, "failed": 0, "skipped": 0, "error": 0}
    assert payload.log is not None and "1 passed" in payload.log
    assert payload.detail == "ok"
