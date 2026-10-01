"""Read-only feed of agent_runs for the Agent Activity screen."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from infra.db.models.agent_run import AgentRun
from infra.db.models.project import Project

router = APIRouter(prefix="/agent-activity", tags=["agent-activity"])


class AgentRunOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    agent_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    output_envelope: dict[str, Any]

    model_config = {"from_attributes": True}


@router.get("/projects/{project_id}", response_model=list[AgentRunOut])
async def list_agent_activity(
    project_id: uuid.UUID,
    agent_name: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[AgentRun]:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    query = select(AgentRun).where(AgentRun.project_id == project_id)
    if agent_name:
        query = query.where(AgentRun.agent_name == agent_name)
    result = await session.execute(query.order_by(AgentRun.started_at.desc()).limit(100))
    return list(result.scalars().all())
