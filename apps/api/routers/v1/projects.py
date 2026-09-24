"""
Project CRUD — the root entity everything else scopes under (project_id).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from infra.db.models.agent_run import AgentRun
from infra.db.models.application_map import ApplicationMap, ApplicationMapState
from infra.db.models.approval import Approval
from infra.db.models.audit_log import AuditLog
from infra.db.models.discovery_credential import DiscoveryCredential
from infra.db.models.project import Project
from infra.db.models.requirement import Requirement, RequirementVersion
from infra.db.models.test_case import TestCase, TestCaseVersion

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreateRequest(BaseModel):
    name: str
    application_url: str | None = None
    credential_ref: str | None = Field(default=None, max_length=200)


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
        name=body.name,
        application_url=body.application_url,
        credential_ref=body.credential_ref,
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


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> None:
    """Hard delete a project and cascade-delete every record scoped to it."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

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
    await session.execute(delete(AgentRun).where(AgentRun.project_id == project_id))
    await session.execute(delete(Approval).where(Approval.project_id == project_id))
    await session.execute(delete(AuditLog).where(AuditLog.project_id == project_id))
    await session.delete(project)
    await session.commit()
