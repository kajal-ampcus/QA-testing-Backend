"""
Project CRUD — the root entity everything else scopes under (project_id).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AnyHttpUrl, BaseModel, Field
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from infra.db.models.agent_run import AgentRun
from infra.db.models.application_map import ApplicationMap, ApplicationMapState
from infra.db.models.approval import Approval
from infra.db.models.audit_log import AuditLog
from infra.db.models.automation import AutomationScript, AutomationVersion
from infra.db.models.discovery_credential import DiscoveryCredential
from infra.db.models.discovery_evidence_review import DiscoveryEvidenceReview
from infra.db.models.discovery_form_answer import DiscoveryFormAnswer
from infra.db.models.execution import TestResult, TestRun
from infra.db.models.project import Project
from infra.db.models.requirement import Requirement, RequirementVersion
from infra.db.models.test_case import TestCase, TestCaseVersion
from infra.queue.broker import get_arq_pool
from infra.queue.discovery_lock import active_discovery_job

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    application_url: AnyHttpUrl | None = None
    # A new project owns no accounts yet; add one via /projects/{id}/credentials,
    # which also sets it as the default.
    credential_ref: None = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    application_url: str | None
    credential_ref: str | None

    model_config = {"from_attributes": True}


class ProjectDetailResponse(ProjectResponse):
    has_application_map: bool = False


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: ProjectCreateRequest, session: AsyncSession = Depends(get_db_session)
) -> Project:
    project = Project(
        name=body.name.strip(),
        application_url=str(body.application_url) if body.application_url else None,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> ProjectDetailResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    has_map = await session.scalar(select(exists().where(ApplicationMap.project_id == project_id)))
    return ProjectDetailResponse.model_validate(project).model_copy(
        update={"has_application_map": bool(has_map)}
    )


@router.get("", response_model=list[ProjectResponse])
async def list_projects(session: AsyncSession = Depends(get_db_session)) -> list[Project]:
    result = await session.execute(select(Project))
    return list(result.scalars().all())


async def _ensure_no_active_discovery(project_id: uuid.UUID) -> None:
    try:
        job_id = await active_discovery_job(await get_arq_pool(), project_id)
    except (RedisConnectionError, RedisTimeoutError, OSError):
        # Without Redis no discovery job can be running.
        return
    if job_id:
        raise HTTPException(
            status_code=409,
            detail="Stop the running discovery for this project before deleting it.",
        )


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> None:
    """Hard delete a project and cascade-delete every record scoped to it."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await _ensure_no_active_discovery(project_id)

    script_ids = select(AutomationScript.id).where(AutomationScript.project_id == project_id)
    run_ids = select(TestRun.id).where(TestRun.project_id == project_id)
    await session.execute(delete(TestResult).where(TestResult.test_run_id.in_(run_ids)))
    await session.execute(delete(TestRun).where(TestRun.project_id == project_id))
    await session.execute(
        delete(AutomationVersion).where(AutomationVersion.automation_script_id.in_(script_ids))
    )
    await session.execute(delete(AutomationScript).where(AutomationScript.project_id == project_id))
    await session.execute(
        delete(TestCaseVersion).where(
            TestCaseVersion.test_case_id.in_(
                select(TestCase.id).where(TestCase.project_id == project_id)
            )
        )
    )
    await session.execute(delete(TestCase).where(TestCase.project_id == project_id))
    await session.execute(
        delete(RequirementVersion).where(
            RequirementVersion.requirement_id.in_(
                select(Requirement.id).where(Requirement.project_id == project_id)
            )
        )
    )
    await session.execute(delete(Requirement).where(Requirement.project_id == project_id))
    await session.execute(
        delete(ApplicationMapState).where(
            ApplicationMapState.application_map_id.in_(
                select(ApplicationMap.id).where(ApplicationMap.project_id == project_id)
            )
        )
    )
    await session.execute(delete(ApplicationMap).where(ApplicationMap.project_id == project_id))
    await session.execute(
        delete(DiscoveryCredential).where(DiscoveryCredential.project_id == project_id)
    )
    await session.execute(
        delete(DiscoveryFormAnswer).where(DiscoveryFormAnswer.project_id == project_id)
    )
    await session.execute(
        delete(DiscoveryEvidenceReview).where(DiscoveryEvidenceReview.project_id == project_id)
    )
    await session.execute(delete(AgentRun).where(AgentRun.project_id == project_id))
    await session.execute(delete(Approval).where(Approval.project_id == project_id))
    await session.execute(delete(AuditLog).where(AuditLog.project_id == project_id))
    await session.delete(project)
    await session.commit()
