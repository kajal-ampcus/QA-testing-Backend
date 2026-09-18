"""
Application Map endpoints — triggers Discovery (enqueued via arq, run in the
separate worker process, never synchronously inside this request — a real
crawl can take minutes) and surfaces the resulting map.
"""

import uuid
from typing import Any

from arq.jobs import Job, JobStatus
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AnyHttpUrl, BaseModel, Field
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from domain.enums import RequirementStatus
from infra.db.models.project import Project
from infra.db.models.requirement import Requirement
from infra.db.repositories.application_map_repo import ApplicationMapRepository
from infra.queue.broker import enqueue, get_arq_pool

router = APIRouter(prefix="/application-maps", tags=["application-maps"])


class DiscoveryTriggerRequest(BaseModel):
    url: AnyHttpUrl | None = None
    credential_ref: str | None = Field(default=None, max_length=200)
    focus_requirements: list[str] = Field(default_factory=list)
    max_pages: int = Field(default=150, ge=1)
    max_depth: int = Field(default=6, ge=0)
    max_duration_seconds: int = Field(default=900, ge=1)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": None,
                    "focus_requirements": [],
                    "max_pages": 150,
                    "max_depth": 6,
                    "max_duration_seconds": 900,
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


class ApplicationMapStateResponse(BaseModel):
    state_code: str
    url_pattern: str
    fingerprint: str
    reached_via: list[str]
    elements: list[dict[str, Any]]


class ApplicationMapResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    version: int
    base_url: str
    status: str
    termination_reason: str | None
    coverage: dict[str, Any]
    states: list[ApplicationMapStateResponse]


@router.post(
    "/projects/{project_id}/discover",
    response_model=DiscoveryTriggerResponse,
    status_code=202,
    responses={
        404: {"description": "Project not found"},
        409: {"description": "Focus requirement is not approved/current"},
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
    payload = {
        "target": {
            "url": target_url,
            "credential_ref": body.credential_ref or project.credential_ref,
        },
        "focus_requirements": approved_refs,
        "crawl_budget": {
            "max_pages": body.max_pages,
            "max_depth": body.max_depth,
            "max_duration_seconds": body.max_duration_seconds,
        },
    }
    try:
        job_id = await enqueue("run_discovery", str(project_id), payload)
    except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
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
    except Exception as exc:  # noqa: BLE001 - job exceptions are internal worker details
        return DiscoveryJobResponse(
            job_id=job_id, status="failed", result={"error": type(exc).__name__}
        )
    return DiscoveryJobResponse(job_id=job_id, status=status.value, result=result)


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
    return ApplicationMapResponse(
        id=app_map.id,
        project_id=app_map.project_id,
        version=app_map.version,
        base_url=app_map.base_url,
        status=app_map.status,
        termination_reason=app_map.termination_reason,
        coverage=app_map.coverage,
        states=[
            ApplicationMapStateResponse(
                state_code=s.state_code,
                url_pattern=s.url_pattern,
                fingerprint=s.fingerprint,
                reached_via=s.reached_via,
                elements=s.elements,
            )
            for s in app_map.states
        ],
    )
