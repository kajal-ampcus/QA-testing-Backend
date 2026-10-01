"""
Agent 9 — Test Reporting. Metrics are counted from the database. Any later
narrative must only describe those numbers.
"""

from collections import Counter
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.db.models.agent_run import AgentRun
from infra.db.models.application_map import ApplicationMap
from infra.db.models.requirement import Requirement
from infra.db.models.test_case import TestCase
from schemas.report import ReportMetrics, ReportResponse


async def project_metrics(session: AsyncSession, project_id: UUID) -> ReportMetrics:
    requirements = (
        await session.execute(
            select(func.count()).select_from(Requirement).where(Requirement.project_id == project_id)
        )
    ).scalar_one()
    approved = (
        await session.execute(
            select(func.count())
            .select_from(Requirement)
            .where(Requirement.project_id == project_id, Requirement.status == "APPROVED")
        )
    ).scalar_one()
    maps = (
        await session.execute(
            select(func.count())
            .select_from(ApplicationMap)
            .where(ApplicationMap.project_id == project_id)
        )
    ).scalar_one()
    cases = list(
        (
            await session.execute(select(TestCase.status).where(TestCase.project_id == project_id))
        ).scalars()
    )
    runs = (
        await session.execute(
            select(func.count()).select_from(AgentRun).where(AgentRun.project_id == project_id)
        )
    ).scalar_one()
    return ReportMetrics(
        requirements=int(requirements),
        approved_requirements=int(approved),
        application_maps=int(maps),
        test_cases=len(cases),
        test_cases_by_status=dict(Counter(cases)),
        agent_runs=int(runs),
    )


async def build_report(session: AsyncSession, project_id: UUID) -> ReportResponse:
    metrics = await project_metrics(session, project_id)
    return ReportResponse(project_id=str(project_id), metrics=metrics, narrative=None)
