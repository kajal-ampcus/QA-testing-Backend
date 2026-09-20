"""
Project CRUD — the root entity everything else scopes under (project_id).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from infra.db.models.application_map import ApplicationMap
from infra.db.models.project import Project

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
