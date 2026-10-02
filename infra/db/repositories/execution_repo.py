"""Test run and result persistence. The caller commits."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from domain.enums import TestRunStatus
from infra.db.models.execution import TestResult, TestRun
from infra.db.repositories.base_repo import BaseRepository

_ACTIVE = (TestRunStatus.QUEUED, TestRunStatus.RUNNING)


class ExecutionRepository(BaseRepository):
    def add_run(self, run: TestRun) -> TestRun:
        self.session.add(run)
        return run

    async def get_run(self, run_id: uuid.UUID) -> TestRun | None:
        result = await self.session.execute(
            select(TestRun).where(TestRun.id == run_id).options(selectinload(TestRun.results))
        )
        return result.scalar_one_or_none()

    async def get_run_for_project(
        self, project_id: uuid.UUID, run_id: uuid.UUID
    ) -> TestRun | None:
        result = await self.session.execute(
            select(TestRun)
            .where(TestRun.id == run_id, TestRun.project_id == project_id)
            .options(selectinload(TestRun.results))
        )
        return result.scalar_one_or_none()

    async def get_by_job(self, job_id: str) -> TestRun | None:
        result = await self.session.execute(
            select(TestRun).where(TestRun.job_id == job_id).options(selectinload(TestRun.results))
        )
        return result.scalar_one_or_none()

    async def list_for_project(self, project_id: uuid.UUID) -> list[TestRun]:
        result = await self.session.execute(
            select(TestRun)
            .where(TestRun.project_id == project_id)
            .options(selectinload(TestRun.results))
            .order_by(TestRun.created_at.desc())
        )
        return list(result.scalars().all())

    async def active_for_generation(
        self, project_id: uuid.UUID, generation_id: uuid.UUID
    ) -> TestRun | None:
        result = await self.session.execute(
            select(TestRun)
            .where(
                TestRun.project_id == project_id,
                TestRun.generation_id == generation_id,
                TestRun.status.in_(_ACTIVE),
            )
            .order_by(TestRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def has_completed_run(
        self, project_id: uuid.UUID, generation_id: uuid.UUID
    ) -> bool:
        result = await self.session.scalar(
            select(TestRun.id)
            .where(
                TestRun.project_id == project_id,
                TestRun.generation_id == generation_id,
                TestRun.status == TestRunStatus.COMPLETED,
            )
            .limit(1)
        )
        return result is not None

    def mark_running(self, run: TestRun) -> None:
        run.status = TestRunStatus.RUNNING
        run.started_at = datetime.now(UTC)

    def mark_finished(
        self,
        run: TestRun,
        status: str,
        summary: dict[str, Any],
    ) -> None:
        run.status = status
        run.summary = summary
        run.finished_at = datetime.now(UTC)

    def add_result(self, result: TestResult) -> TestResult:
        self.session.add(result)
        return result
