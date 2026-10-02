"""
Application Map endpoints — triggers Discovery (enqueued via arq, run in the
separate worker process, never synchronously inside this request — a real
crawl can take minutes) and surfaces the resulting map.
"""

import asyncio
import os
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from arq.jobs import Job, JobStatus
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import AnyHttpUrl, BaseModel, Field
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from core.policy_safety.environment_policy import DiscoveryTargetError, validate_discovery_target
from domain.enums import RequirementStatus
from infra.db.models.discovery_credential import DiscoveryCredential
from infra.db.models.project import Project
from infra.db.models.requirement import Requirement
from infra.db.repositories.application_map_repo import ApplicationMapRepository
from infra.queue.broker import enqueue, get_arq_pool
from infra.queue.discovery_lock import (
    active_discovery_job,
    claim_discovery,
    clear_inactive_discovery,
    release_discovery,
)

_REDIS_ERRORS = (RedisConnectionError, RedisTimeoutError, OSError)

router = APIRouter(prefix="/application-maps", tags=["application-maps"])


class DiscoveryTriggerRequest(BaseModel):
    url: AnyHttpUrl | None = None
    credential_ref: str | None = Field(default=None, max_length=200)
    focus_requirements: list[str] = Field(default_factory=list)
    max_pages: int = Field(default=150, ge=1)
    max_depth: int = Field(default=6, ge=0)
    max_duration_seconds: int = Field(default=900, ge=1)
    worker_limit: int = Field(default=3, ge=1, le=5)
    automatic_limits: bool = True
    discovery_mode: str = Field(
        default="entry_points",
        pattern="^(targeted|full|entry_points|auth_flow|modules|inventory|deep|complete)$",
    )
    selected_auth_flow: str | None = Field(default=None, max_length=200)
    selected_auth_flows: list[str] = Field(default_factory=list, max_length=20)
    selected_areas: list[str] = Field(default_factory=list)
    selected_modules: list[str] = Field(default_factory=list)
    resume_application_map_id: uuid.UUID | None = None
    start_from_scratch: bool = False

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": None,
                    "focus_requirements": [],
                    "max_pages": 150,
                    "max_depth": 6,
                    "max_duration_seconds": 900,
                    "automatic_limits": True,
                }
            ]
        }
    }


class DiscoveryTriggerResponse(BaseModel):
    job_id: str


class DiscoveryJobResponse(BaseModel):
    job_id: str
    status: str
    result: dict[str, Any] | None = None


class DiscoveryCancelResponse(BaseModel):
    job_id: str
    status: str


class ApplicationMapStateResponse(BaseModel):
    state_code: str
    url_pattern: str
    fingerprint: str
    reached_via: list[str]
    elements: list[dict[str, Any]]
    evidence_ref: str | None = None


class ApplicationMapResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    base_url: str
    status: str
    termination_reason: str | None
    coverage: dict[str, Any]
    states: list[ApplicationMapStateResponse]
    diagnostic_evidence: dict[str, Any] | None = None
    discovery_checkpoint: dict[str, Any] | None = None
    test_generation_coverage: dict[str, list[str]] = Field(default_factory=dict)
    project_test_generation_coverage: dict[str, list[str]] = Field(default_factory=dict)


async def _check_target(url: str) -> None:
    try:
        await validate_discovery_target(url)
    except DiscoveryTargetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _resolve_credential(
    session: AsyncSession,
    project: Project,
    credential_ref: str | None,
) -> str | None:
    """Use an active account for this project.

    Editing an account creates a new credential id and deactivates the old one.
    A discover request that still carries the retired id follows the project's
    current default instead of failing.
    """
    requested = credential_ref or project.credential_ref
    if not requested:
        return None

    async def active(ref: str) -> bool:
        owned = await session.scalar(
            select(DiscoveryCredential.id).where(
                DiscoveryCredential.project_id == project.id,
                DiscoveryCredential.credential_ref == ref,
                DiscoveryCredential.active.is_(True),
            )
        )
        return owned is not None

    if await active(requested):
        return requested
    replacement = project.credential_ref
    if replacement and replacement != requested and await active(replacement):
        return replacement
    raise HTTPException(
        status_code=422,
        detail="Test account is not an active account of this project",
    )


async def _claim_discovery_slot(project_id: uuid.UUID) -> str:
    try:
        job_id = await claim_discovery(await get_arq_pool(), project_id)
    except _REDIS_ERRORS as exc:
        raise HTTPException(status_code=503, detail="Discovery queue is unavailable") from exc
    if job_id is None:
        raise HTTPException(
            status_code=409,
            detail="Discovery is already running for this project. Stop it or wait for it to finish.",
        )
    return job_id


async def _release_discovery_slot(project_id: uuid.UUID, job_id: str) -> None:
    with suppress(*_REDIS_ERRORS):
        await release_discovery(await get_arq_pool(), project_id, job_id)


@router.post(
    "/projects/{project_id}/discover",
    response_model=DiscoveryTriggerResponse,
    status_code=202,
    responses={
        404: {"description": "Project not found"},
        409: {"description": "Focus requirement is not approved/current, or discovery is running"},
        422: {"description": "Target URL or test account is not allowed"},
        503: {"description": "Discovery queue is unavailable"},
    },
)
async def trigger_discovery(
    project_id: uuid.UUID,
    body: DiscoveryTriggerRequest,
    session: AsyncSession = Depends(get_db_session),
) -> DiscoveryTriggerResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if body.url is None and not project.application_url:
        raise HTTPException(
            status_code=422,
            detail="Provide a discovery URL or set the project's application_url",
        )
    target_url = str(body.url) if body.url is not None else str(project.application_url)
    if body.resume_application_map_id:
        resumable = await ApplicationMapRepository(session).get_with_states(
            body.resume_application_map_id
        )
        if resumable is None or resumable.project_id != project_id:
            raise HTTPException(status_code=404, detail="Discovery checkpoint not found")
        if resumable.status != "PARTIAL" or not resumable.discovery_checkpoint:
            raise HTTPException(status_code=409, detail="Discovery is not resumable")
        target_url = resumable.base_url
    approved_refs: list[str] = []
    for ref in body.focus_requirements:
        code, _, version_ref = ref.partition("@v")
        try:
            requirement_id = uuid.UUID(code)
        except ValueError:
            requirement_id = None
        query = select(Requirement).where(Requirement.project_id == project_id)
        query = query.where(
            Requirement.id == requirement_id if requirement_id else Requirement.req_code == code
        )
        requirement = (await session.execute(query)).scalar_one_or_none()
        if requirement is None:
            raise HTTPException(status_code=422, detail=f"Unknown focus requirement: {ref}")
        if requirement.status != RequirementStatus.APPROVED:
            raise HTTPException(status_code=409, detail=f"Focus requirement is not approved: {ref}")
        if version_ref and version_ref != str(requirement.current_version):
            raise HTTPException(
                status_code=409, detail=f"Focus requirement version is not current: {ref}"
            )
        approved_refs.append(f"{requirement.id}@v{requirement.current_version}")
    await _check_target(target_url)
    credential_ref = await _resolve_credential(
        session, project, body.credential_ref or project.credential_ref
    )
    payload = {
        "target": {
            "url": target_url,
            "credential_ref": credential_ref,
        },
        "focus_requirements": approved_refs,
        "crawl_budget": {
            "max_pages": body.max_pages,
            "max_depth": body.max_depth,
            "max_duration_seconds": body.max_duration_seconds,
            "worker_limit": body.worker_limit,
            "automatic_limits": body.automatic_limits,
        },
        "discovery_scope": {
            "mode": body.discovery_mode,
            "selected_auth_flow": body.selected_auth_flow,
            "selected_auth_flows": body.selected_auth_flows,
            "selected_areas": body.selected_areas,
            "selected_modules": body.selected_modules,
        },
        "resume_application_map_id": (
            str(body.resume_application_map_id)
            if body.resume_application_map_id
            else None
        ),
        "start_from_scratch": body.start_from_scratch,
    }
    claimed_job_id = await _claim_discovery_slot(project_id)
    try:
        job_id = await enqueue("run_discovery", str(project_id), payload, job_id=claimed_job_id)
    except (*_REDIS_ERRORS, RuntimeError) as exc:
        await _release_discovery_slot(project_id, claimed_job_id)
        raise HTTPException(status_code=503, detail="Discovery queue is unavailable") from exc
    return DiscoveryTriggerResponse(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=DiscoveryJobResponse)
async def get_discovery_job(job_id: str) -> DiscoveryJobResponse:
    job = Job(job_id, await get_arq_pool())
    status = await job.status()
    if status == JobStatus.not_found:
        raise HTTPException(status_code=404, detail="Discovery job not found")
    if status != JobStatus.complete:
        return DiscoveryJobResponse(job_id=job_id, status=status.value)
    try:
        result = await job.result()
    except asyncio.CancelledError:
        return DiscoveryJobResponse(job_id=job_id, status="cancelled", result=None)
    except Exception as exc:  # noqa: BLE001 - job exceptions are internal worker details
        return DiscoveryJobResponse(
            job_id=job_id, status="failed", result={"error": type(exc).__name__}
        )
    return DiscoveryJobResponse(job_id=job_id, status=status.value, result=result)


async def _close_running_map(session: AsyncSession, project_id: uuid.UUID) -> bool:
    """Finish a map left RUNNING after the worker process is already gone."""
    repo = ApplicationMapRepository(session)
    app_map = await repo.get_latest_for_project(project_id)
    if app_map is None or app_map.status != "RUNNING":
        return False
    states = await repo.count_states(app_map.id)
    diagnostic = dict(app_map.diagnostic_evidence or {})
    diagnostic["termination_detail"] = (
        "Discovery was stopped. The saved pages can be continued."
    )
    await repo.set_status(
        app_map.id,
        "PARTIAL",
        termination_reason="CANCELLED_BY_USER",
        coverage={
            **(app_map.coverage or {}),
            "cancelled": True,
            "recoverable": True,
            "states_discovered": states,
        },
        diagnostic_evidence=diagnostic,
    )
    await session.commit()
    return True


@router.delete("/projects/{project_id}/discover", response_model=DiscoveryCancelResponse)
async def stop_project_discovery(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> DiscoveryCancelResponse:
    """Stop the project's crawl, including a run whose worker has already exited."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        pool = await get_arq_pool()
        job_id = await active_discovery_job(pool, project_id)
    except _REDIS_ERRORS as exc:
        raise HTTPException(status_code=503, detail="Discovery queue is unavailable") from exc
    if job_id:
        try:
            cancelled = await Job(job_id, pool).abort(timeout=5)
        except TimeoutError:
            return DiscoveryCancelResponse(job_id=job_id, status="cancellation_requested")
        if cancelled:
            return DiscoveryCancelResponse(job_id=job_id, status="cancelled")
    closed = await _close_running_map(session, project_id)
    with suppress(*_REDIS_ERRORS):
        await clear_inactive_discovery(pool, project_id)
    return DiscoveryCancelResponse(
        job_id=job_id or "",
        status="stopped" if closed else "already_finished",
    )


@router.delete("/jobs/{job_id}", response_model=DiscoveryCancelResponse)
async def cancel_discovery_job(job_id: str) -> DiscoveryCancelResponse:
    """Cancel a queued or running discovery without stopping the worker process."""
    job = Job(job_id, await get_arq_pool())
    status = await job.status()
    if status == JobStatus.not_found:
        raise HTTPException(status_code=404, detail="Discovery job not found")
    if status == JobStatus.complete:
        return DiscoveryCancelResponse(job_id=job_id, status="already_finished")

    try:
        cancelled = await job.abort(timeout=5)
    except TimeoutError:
        # ARQ has recorded the abort request, but a browser/MCP call may not
        # yield quickly enough for Job.abort() to observe worker completion.
        # Cancellation is asynchronous; polling will report the terminal state.
        return DiscoveryCancelResponse(job_id=job_id, status="cancellation_requested")
    if not cancelled:
        return DiscoveryCancelResponse(job_id=job_id, status="already_finished")
    return DiscoveryCancelResponse(job_id=job_id, status="cancelled")


@router.get("/evidence/{filename}", response_class=FileResponse)
async def get_discovery_evidence(filename: str) -> FileResponse:
    """Serve a discovery screenshot without permitting arbitrary file access."""
    try:
        screenshot_id = uuid.UUID(Path(filename).stem)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Discovery evidence not found") from exc
    if filename != f"{screenshot_id}.png":
        raise HTTPException(status_code=404, detail="Discovery evidence not found")
    directory = Path(os.environ.get("DISCOVERY_EVIDENCE_DIR", "artifacts/discovery")).resolve()
    target = directory / filename
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Discovery evidence not found")
    return FileResponse(target, media_type="image/png", filename=filename)


@router.get("/projects/{project_id}", response_model=ApplicationMapResponse)
async def get_latest_map(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> ApplicationMapResponse:
    repo = ApplicationMapRepository(session)
    app_map = await repo.get_latest_for_project(project_id)
    if app_map is None:
        raise HTTPException(
            status_code=404,
            detail="No application map yet for this project — trigger discovery first",
        )
    project_generation_coverage = await repo.project_generation_coverage(project_id)
    return ApplicationMapResponse(
        id=app_map.id,
        project_id=app_map.project_id,
        version=app_map.version,
        base_url=app_map.base_url,
        status=app_map.status,
        termination_reason=app_map.termination_reason,
        coverage=app_map.coverage,
        diagnostic_evidence=app_map.diagnostic_evidence,
        states=[
            ApplicationMapStateResponse(
                state_code=s.state_code,
                url_pattern=s.url_pattern,
                fingerprint=s.fingerprint,
                reached_via=s.reached_via,
                elements=s.elements,
                evidence_ref=s.evidence_ref,
            )
            for s in app_map.states
        ],
        discovery_checkpoint=app_map.discovery_checkpoint,
        test_generation_coverage=app_map.test_generation_coverage,
        project_test_generation_coverage=project_generation_coverage,
    )
