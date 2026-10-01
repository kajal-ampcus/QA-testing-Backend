"""
Project scope helper (architecture doc Section 28). Routes that take a
project_id should load the project through this dependency so a missing or
cross-tenant id fails the same way everywhere.
"""

from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from infra.db.models.project import Project


async def require_project(
    project_id: UUID, session: AsyncSession = Depends(get_db_session)
) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
