"""Report endpoints — deterministic metrics from the current project snapshot."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from core.agents.reporting.agent import build_report
from infra.db.models.project import Project
from schemas.report import ReportResponse

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/projects/{project_id}", response_model=ReportResponse)
async def get_project_report(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> ReportResponse:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return await build_report(session, project_id)
