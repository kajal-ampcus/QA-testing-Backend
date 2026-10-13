"""
Test execution endpoints — triggers Test Execution Agent (enqueues
apps/worker/tasks/run_execution.py) and surfaces test_runs/test_results
(Section 14).
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

from arq.jobs import Job, JobStatus
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, StrictStr
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from apps.api.routers.v1.automation import artifact_root
from apps.api.settings import ApiSettings
from core.agents.automation_generation.artifacts import ArtifactPathError, generation_dir
from core.agents.automation_generation.registry import execution_available
from core.agents.reporting.execution_report import (
    load_execution_report,
    run_is_finished,
)
from core.agents.test_execution.eligibility import (
    ExecutionEligibilityError,
    ScriptSnapshot,
    classify_scripts,
    resolve_pending_script_ids,
)
from core.agents.test_execution.live_display import (
    current_live_display,
    current_live_log,
    release_live_display,
)
from domain.enums import EvidenceChannel, TestRunStatus
from infra.db.models.execution import TestResult, TestRun
from infra.db.models.project import Project
from infra.db.models.test_case import TestCase
from infra.db.repositories.automation_repo import AutomationRepository
from infra.db.repositories.execution_repo import ExecutionRepository
from infra.execution_inputs import (
    public_requests,
    read_json,
    run_input_dir,
    validate_answer,
    write_json,
)
from infra.object_storage.s3_client import S3Client
from infra.queue.broker import enqueue, get_arq_pool
from schemas.execution import (
    AssertionOut,
    EvidenceOut,
    ExecutionCancelResponse,
    ExecutionJobResponse,
    ExecutionListOut,
    ExecutionReportOut,
    LiveExecutionOut,
    TestResultOut,
    TestRunDetailOut,
    TestRunSummaryOut,
    TriggerExecutionRequest,
    TriggerExecutionResponse,
)

_REDIS_ERRORS = (RedisConnectionError, RedisTimeoutError, OSError)
_MEDIA = {
    EvidenceChannel.SCREENSHOT.value: ("image/png", ".png"),
    EvidenceChannel.VIDEO.value: ("video/webm", ".webm"),
    EvidenceChannel.TRACE.value: ("application/zip", ".zip"),
    EvidenceChannel.CONSOLE_LOG.value: ("application/json", ".json"),
    EvidenceChannel.NETWORK_LOG.value: ("application/json", ".json"),
}

router = APIRouter(prefix="/executions", tags=["executions"])


class ExecutionInputAnswer(BaseModel):
    mode: str = "value"
    value: StrictStr = Field(max_length=10000)


class ExecutionInputOverrides(BaseModel):
    expected_revision: int = Field(ge=0)
    values: dict[str, ExecutionInputAnswer | None] = Field(max_length=200)


async def _input_suite(project_id: uuid.UUID, generation_id: uuid.UUID, session: AsyncSession):
    if await session.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    suite = generation_dir(artifact_root(), project_id, generation_id)
    manifest = read_json(suite / "manifest.json")
    if not manifest:
        raise HTTPException(404, "Automation generation not found")
    return suite, manifest


@router.get("/projects/{project_id}/generations/{generation_id}/inputs")
async def get_execution_input_draft(
    project_id: uuid.UUID, generation_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    suite, manifest = await _input_suite(project_id, generation_id, session)
    draft = read_json(suite / "execution-inputs.json")
    bindings = manifest.get("input_bindings", [])
    sensitive = {item["key"] for item in bindings if item.get("sensitive")}
    values = {
        key: {**value, "value": "" if key in sensitive else value.get("value", "")}
        for key, value in draft.get("values", {}).items()
    }
    return {"revision": draft.get("revision", 0), "bindings": bindings, "values": values,
            "supported": manifest.get("input_runtime_version") == 1}


@router.post("/projects/{project_id}/generations/{generation_id}/inputs")
async def save_execution_input_draft(
    project_id: uuid.UUID, generation_id: uuid.UUID, body: ExecutionInputOverrides,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    # Serialize API edits for this project's file; optimistic revision protects
    # stale browser tabs. A run already started uses its own immutable snapshot.
    await session.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    suite, manifest = await _input_suite(project_id, generation_id, session)
    bindings = {item["key"]: item for item in manifest.get("input_bindings", [])}
    draft = read_json(suite / "execution-inputs.json")
    if draft.get("revision", 0) != body.expected_revision:
        raise HTTPException(409, "Input draft changed; reload before saving")
    values = dict(draft.get("values", {}))
    for key, value in body.values.items():
        if key not in bindings:
            raise HTTPException(422, "Unknown or stale input binding")
        if value is None:
            values.pop(key, None)
        else:
            try:
                values[key] = validate_answer(value.model_dump(), bindings[key])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
    write_json(suite / "execution-inputs.json", {
        "schema_version": 1, "revision": body.expected_revision + 1, "values": values,
    })
    await session.commit()
    return {"revision": body.expected_revision + 1}


async def _input_directory(project_id: uuid.UUID, run_id: uuid.UUID, session: AsyncSession):
    run = await ExecutionRepository(session).get_run_for_project(project_id, run_id)
    if run is None:
        raise HTTPException(404, "Test run not found")
    suite = generation_dir(artifact_root(), project_id, run.generation_id)
    return run, run_input_dir(suite, run_id)


@router.get("/projects/{project_id}/runs/{run_id}/input-requests")
async def get_execution_inputs(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    run, directory = await _input_directory(project_id, run_id, session)
    if run.status not in {TestRunStatus.QUEUED, TestRunStatus.RUNNING}:
        return {"requests": []}
    requests = public_requests(directory)
    for item in requests:
        if time.time() >= item["expires_at"]:
            item["status"] = "EXPIRED"
    return {"requests": requests}


@router.post("/projects/{project_id}/runs/{run_id}/input-requests/{request_id}")
async def answer_execution_input(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    request_id: uuid.UUID,
    body: ExecutionInputAnswer,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    run, directory = await _input_directory(project_id, run_id, session)
    if run.status != TestRunStatus.RUNNING:
        raise HTTPException(409, "This run is no longer waiting for input")
    identifier = str(request_id)
    pending = read_json(directory / f"{identifier}.request.json")
    if not pending:
        raise HTTPException(404, "Execution input request not found")
    if (
        pending.get("status") != "WAITING_FOR_INPUT"
        or time.time() >= pending["expires_at"]
        or (directory / f"{identifier}.consumed.json").exists()
    ):
        raise HTTPException(409, "This input request is already resolved or expired")
    binding = read_json(directory / "bindings.json").get(pending["key"])
    if not binding:
        raise HTTPException(409, "Input binding changed; regenerate the suite")
    try:
        answer = validate_answer(body.model_dump(), binding)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Publish a fully written answer exclusively, so concurrent submissions do
    # not overwrite one another and the browser cannot read partial JSON.
    temporary = directory / f".{uuid.uuid4()}.answer.tmp"
    write_json(temporary, {**answer, "source": "manual"})
    try:
        os.link(temporary, directory / f"{identifier}.manual.json")
    except FileExistsError as exc:
        raise HTTPException(409, "An answer was already submitted") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "RESOLVED"}


async def start_execution_run(
    session: AsyncSession,
    project: Project,
    generation_id: uuid.UUID,
    *,
    run_destructive: bool = False,
    script_ids: list[uuid.UUID] | None = None,
) -> TriggerExecutionResponse:
    """Queue the Test Execution Agent. The worker runs npx playwright test."""
    scripts = await AutomationRepository(session).list_generation(project.id, generation_id)
    if not scripts:
        raise HTTPException(status_code=404, detail="Automation generation not found")
    summary = dict(scripts[0].suite_summary or {})
    if not execution_available(str(summary.get("language") or ""), scripts[0].framework):
        raise HTTPException(
            status_code=409,
            detail=(
                "Execution is not available for this language and framework yet. "
                "Download the suite and run it locally."
            ),
        )
    explicit_ids = script_ids
    script_ids = resolve_pending_script_ids(
        script_ids, (scripts[0].suite_summary or {}).get("pending_script_ids")
    )
    used_pending = explicit_ids is None and script_ids is not None
    snapshots = [
        ScriptSnapshot(
            id=script.id,
            file_path=script.file_path,
            risk_level=script.risk_level,
            review_status=script.review_status,
            test_case_code=script.test_case_code,
        )
        for script in scripts
    ]
    try:
        classify_scripts(snapshots, script_ids, run_destructive)
    except ExecutionEligibilityError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc

    if used_pending:
        for script in scripts:
            summary = dict(script.suite_summary or {})
            summary.pop("pending_script_ids", None)
            script.suite_summary = summary

    execution_repo = ExecutionRepository(session)
    active = await execution_repo.active_for_generation(project.id, generation_id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail="An execution is already running for this generation. Wait for it to finish.",
        )

    try:
        suite = generation_dir(artifact_root(), project.id, generation_id)
    except ArtifactPathError as exc:
        raise HTTPException(
            status_code=404, detail="Generated suite files are not available."
        ) from exc
    if not suite.is_dir():
        raise HTTPException(status_code=404, detail="Generated suite files are not available.")

    settings = ApiSettings()
    job_id = str(uuid.uuid4())
    run = TestRun(
        id=uuid.uuid4(),
        project_id=project.id,
        generation_id=generation_id,
        job_id=job_id,
        environment=settings.environment,
        base_url=project.application_url,
        run_destructive=run_destructive,
        status=TestRunStatus.QUEUED,
        summary={},
    )
    execution_repo.add_run(run)
    await session.commit()
    payload: dict[str, Any] = {
        "run_id": str(run.id),
        "generation_id": str(generation_id),
        "suite_dir": str(suite),
        "run_destructive": run_destructive,
        "script_ids": [str(item) for item in script_ids] if script_ids else None,
        "credential_ref": project.credential_ref,
        "base_url": project.application_url,
        "environment": settings.environment,
    }
    cases = await session.scalars(
        select(TestCase).where(
            TestCase.project_id == project.id,
            TestCase.id.in_([script.test_case_id for script in scripts]),
        )
    )
    account_by_case = {case.id: case.credential_ref for case in cases}
    payload["script_credential_refs"] = {
        str(script.id): account_by_case.get(script.test_case_id) or project.credential_ref
        for script in scripts
    }
    try:
        await enqueue("run_execution", str(project.id), payload, job_id=job_id)
    except (*_REDIS_ERRORS, RuntimeError) as exc:
        stored = await session.get(TestRun, run.id)
        if stored is not None:
            stored.status = TestRunStatus.FAILED
            await session.commit()
        raise HTTPException(status_code=503, detail="Execution queue is unavailable") from exc
    return TriggerExecutionResponse(job_id=job_id, run_id=run.id, status=TestRunStatus.QUEUED)


@router.post(
    "/projects/{project_id}",
    response_model=TriggerExecutionResponse,
    status_code=202,
)
async def trigger_execution(
    project_id: uuid.UUID,
    body: TriggerExecutionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> TriggerExecutionResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return await start_execution_run(
        session,
        project,
        body.generation_id,
        run_destructive=body.run_destructive,
        script_ids=body.script_ids,
    )


@router.get("/jobs/{job_id}", response_model=ExecutionJobResponse)
async def get_execution_job(
    job_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ExecutionJobResponse:
    stored = await ExecutionRepository(session).get_by_job(job_id)
    job = Job(job_id, await get_arq_pool())
    try:
        status = await job.status()
    except _REDIS_ERRORS:
        status = JobStatus.not_found
    if stored is not None and stored.status in {
        TestRunStatus.COMPLETED,
        TestRunStatus.FAILED,
        TestRunStatus.CANCELLED,
    }:
        mapped = "complete" if stored.status == TestRunStatus.COMPLETED else stored.status.lower()
        return ExecutionJobResponse(
            job_id=job_id,
            status=mapped,
            run_id=stored.id,
            result={"status": stored.status, "summary": stored.summary},
        )
    if status == JobStatus.not_found and stored is None:
        raise HTTPException(status_code=404, detail="Execution job not found")
    if status == JobStatus.not_found and stored is not None:
        return ExecutionJobResponse(
            job_id=job_id,
            status=stored.status.lower(),
            run_id=stored.id,
            result={"status": stored.status, "summary": stored.summary},
        )
    if status != JobStatus.complete:
        return ExecutionJobResponse(
            job_id=job_id,
            status=status.value,
            run_id=None if stored is None else stored.id,
        )
    try:
        result = await job.result()
    except asyncio.CancelledError:
        return ExecutionJobResponse(
            job_id=job_id,
            status="cancelled",
            run_id=None if stored is None else stored.id,
        )
    except Exception as exc:  # noqa: BLE001 - job exceptions are internal worker details
        return ExecutionJobResponse(
            job_id=job_id,
            status="failed",
            run_id=None if stored is None else stored.id,
            result={"error": type(exc).__name__},
        )
    return ExecutionJobResponse(
        job_id=job_id,
        status=status.value,
        run_id=None if stored is None else stored.id,
        result=result if isinstance(result, dict) else {"value": result},
    )


@router.get("/live", response_model=LiveExecutionOut)
async def live_execution() -> LiveExecutionOut:
    """Project that currently owns the shared execution browser."""
    owner = await current_live_display()
    if owner is None:
        return LiveExecutionOut()
    project_id, run_id = owner
    return LiveExecutionOut(
        project_id=project_id,
        run_id=run_id,
        activity=await current_live_log(str(run_id)),
    )


@router.delete("/projects/{project_id}", response_model=ExecutionCancelResponse)
async def stop_execution(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ExecutionCancelResponse:
    """Stop this project's queued or running suite without starting another."""
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    repo = ExecutionRepository(session)
    runs = await repo.list_for_project(project_id)
    active = next(
        (run for run in runs if run.status in {TestRunStatus.QUEUED, TestRunStatus.RUNNING}),
        None,
    )
    if active is None:
        return ExecutionCancelResponse(job_id="", status="already_finished")
    job_id = active.job_id or ""
    if job_id:
        try:
            await Job(job_id, await get_arq_pool()).abort(timeout=5)
        except TimeoutError:
            pass
        except _REDIS_ERRORS as exc:
            raise HTTPException(status_code=503, detail="Execution queue is unavailable") from exc
    summary = {
        "passed": int((active.summary or {}).get("passed") or 0),
        "failed": int((active.summary or {}).get("failed") or 0),
        "skipped": int((active.summary or {}).get("skipped") or 0),
        "error": int((active.summary or {}).get("error") or 0),
        "detail": "Execution was stopped.",
    }
    if active.status in {TestRunStatus.QUEUED, TestRunStatus.RUNNING}:
        repo.mark_finished(active, TestRunStatus.CANCELLED, summary)
    await release_live_display(str(active.id))
    await session.commit()
    return ExecutionCancelResponse(job_id=job_id, status="cancelled")


@router.get("/projects/{project_id}", response_model=ExecutionListOut)
async def list_executions(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ExecutionListOut:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    runs = await ExecutionRepository(session).list_for_project(project_id)
    return ExecutionListOut(runs=[_summary(run) for run in runs])


@router.get("/projects/{project_id}/runs/{run_id}", response_model=TestRunDetailOut)
async def get_execution_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> TestRunDetailOut:
    run = await ExecutionRepository(session).get_run_for_project(project_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    return _detail(run)


@router.get(
    "/projects/{project_id}/runs/{run_id}/report",
    response_model=ExecutionReportOut,
)
async def get_execution_report(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ExecutionReportOut:
    """Playwright execution report for one finished run."""
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    run = await ExecutionRepository(session).get_run_for_project(project_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    if not run_is_finished(run.status):
        raise HTTPException(
            status_code=409,
            detail="The execution report is available after the Playwright run finishes.",
        )
    return await load_execution_report(session, run)


@router.get("/projects/{project_id}/runs/{run_id}/results/{result_id}/evidence/{channel}")
async def get_evidence(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    result_id: uuid.UUID,
    channel: str,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    try:
        EvidenceChannel(channel)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Unknown evidence channel") from exc
    run = await ExecutionRepository(session).get_run_for_project(project_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    row = next((item for item in run.results if item.id == result_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Test result not found")
    key = (row.evidence or {}).get(channel)
    if not key:
        raise HTTPException(status_code=404, detail="Evidence is not available for this channel")
    try:
        data = await S3Client().get(str(key))
    except Exception as exc:  # noqa: BLE001 - storage misses become 404
        raise HTTPException(status_code=404, detail="Evidence object was not found") from exc
    media, suffix = _MEDIA[channel]
    # A screenshot link from the Excel report should open the picture in the
    # browser. The other evidence files stay downloads.
    shown = "inline" if channel == EvidenceChannel.SCREENSHOT.value else "attachment"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'{shown}; filename="{channel}{suffix}"'},
    )


def _summary(run: TestRun) -> TestRunSummaryOut:
    raw = dict(run.summary or {})
    log = raw.pop("log", None)
    detail = raw.pop("detail", None)
    return TestRunSummaryOut(
        id=run.id,
        generation_id=run.generation_id,
        job_id=run.job_id,
        environment=run.environment,
        base_url=run.base_url,
        run_destructive=run.run_destructive,
        status=run.status,
        summary=_counts(raw),
        log=_text(log),
        detail=_text(detail),
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        result_count=len(run.results or []),
    )


def _counts(raw: dict[str, Any]) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    for key in counts:
        try:
            counts[key] = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            counts[key] = 0
    return counts


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _detail(run: TestRun) -> TestRunDetailOut:
    base = _summary(run)
    return TestRunDetailOut(
        **base.model_dump(),
        results=[_result_out(item) for item in run.results],
    )


def _result_out(row: TestResult) -> TestResultOut:
    assertion = row.assertion or {}
    evidence = row.evidence or {}
    return TestResultOut(
        id=row.id,
        automation_script_id=row.automation_script_id,
        spec_path=row.spec_path,
        status=row.status,
        assertion=AssertionOut(
            expected=str(assertion.get("expected") or ""),
            actual=str(assertion.get("actual") or ""),
            source=str(assertion.get("source") or "TEST_EXECUTION"),
        ),
        category=str(assertion.get("category") or ""),
        title=str(assertion.get("title") or ""),
        inputs=[
            {"name": str(item.get("name") or ""), "value": str(item.get("value") or "")}
            for item in (assertion.get("inputs") or [])
            if isinstance(item, dict)
        ],
        cause=str(assertion.get("cause") or ""),
        recommendation=str(assertion.get("recommendation") or ""),
        evidence=EvidenceOut(
            screenshot=evidence.get("screenshot"),
            video=evidence.get("video"),
            trace=evidence.get("trace"),
            console_log=evidence.get("console_log"),
            network_log=evidence.get("network_log"),
        ),
        duration_ms=row.duration_ms,
        error_message=row.error_message,
    )
