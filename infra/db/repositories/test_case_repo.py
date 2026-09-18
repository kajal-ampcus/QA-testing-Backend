"""
TestCase repository. Handles append-only test_case_versions writes and
TC-code allocation. Does not commit internally — caller owns the transaction.
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from infra.db.models.test_case import TestCase, TestCaseVersion
from infra.db.repositories.base_repo import BaseRepository


class TestCaseRepository(BaseRepository):

    async def next_tc_code(self, project_id: uuid.UUID) -> str:
        result = await self.session.execute(
            select(func.count())
            .select_from(TestCase)
            .where(TestCase.project_id == project_id)
        )
        count = result.scalar_one()
        return f"TC-{count + 1:03d}"

    async def create(
        self,
        project_id: uuid.UUID,
        tc_code: str,
        requirement_id: uuid.UUID,
        requirement_version: int,
        application_map_id: uuid.UUID,
        version_data: dict[str, Any],
    ) -> tuple[TestCase, TestCaseVersion]:
        test_case = TestCase(
            project_id=project_id,
            tc_code=tc_code,
            requirement_id=requirement_id,
            requirement_version=requirement_version,
            application_map_id=application_map_id,
            current_version=1,
            status="DRAFT",
        )
        self.session.add(test_case)
        await self.session.flush()

        version = TestCaseVersion(
            test_case_id=test_case.id,
            version=1,
            title=version_data["title"],
            objective=version_data["objective"],
            preconditions=version_data.get("preconditions", []),
            steps=version_data["steps"],
            expected_result=version_data["expected_result"],
            test_data=version_data.get("test_data", {}),
            traceability=version_data.get("traceability", []),
            category=version_data.get("category", "POSITIVE"),
            confidence=version_data.get("confidence", 0.0),
        )
        self.session.add(version)
        await self.session.flush()
        return test_case, version

    async def list_for_project(
        self, project_id: uuid.UUID
    ) -> list[tuple[TestCase, TestCaseVersion]]:
        result = await self.session.execute(
            select(TestCase)
            .where(TestCase.project_id == project_id)
            .options(selectinload(TestCase.versions))
            .order_by(TestCase.created_at)
        )
        test_cases = list(result.scalars().all())
        pairs = []
        for tc in test_cases:
            current = next(
                (v for v in tc.versions if v.version == tc.current_version), None
            )
            if current:
                pairs.append((tc, current))
        return pairs

    async def get_with_current_version(
        self, test_case_id: uuid.UUID
    ) -> tuple[TestCase, TestCaseVersion] | None:
        tc = await self.session.get(TestCase, test_case_id)
        if tc is None:
            return None
        result = await self.session.execute(
            select(TestCaseVersion).where(
                TestCaseVersion.test_case_id == test_case_id,
                TestCaseVersion.version == tc.current_version,
            )
        )
        version = result.scalar_one_or_none()
        if version is None:
            return None
        return tc, version

    async def list_for_requirement(
        self, requirement_id: uuid.UUID
    ) -> list[tuple[TestCase, TestCaseVersion]]:
        result = await self.session.execute(
            select(TestCase)
            .where(TestCase.requirement_id == requirement_id)
            .options(selectinload(TestCase.versions))
        )
        test_cases = list(result.scalars().all())
        pairs = []
        for tc in test_cases:
            current = next(
                (v for v in tc.versions if v.version == tc.current_version), None
            )
            if current:
                pairs.append((tc, current))
        return pairs
