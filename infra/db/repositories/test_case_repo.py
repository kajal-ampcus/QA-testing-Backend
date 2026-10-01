"""
TestCase repository. Handles append-only test_case_versions writes and
TC-code allocation. Does not commit internally — caller owns the transaction.
"""

import uuid
from typing import Any

from sqlalchemy import Integer, cast, delete, func, select, update
from sqlalchemy.orm import selectinload

from domain.enums import ApprovalStatus, TestCaseStatus
from infra.db.models.approval import Approval
from infra.db.models.project import Project
from infra.db.models.test_case import TestCase, TestCaseVersion
from infra.db.repositories.base_repo import BaseRepository

_VERSION_FIELDS = (
    "title",
    "objective",
    "preconditions",
    "steps",
    "expected_result",
    "test_data",
    "traceability",
    "category",
)


def case_signature(version_data: dict[str, Any]) -> tuple[Any, ...]:
    """Identity of a case's content, used to avoid saving the same draft twice."""
    return (
        str(version_data.get("category", "")),
        tuple(sorted(str(item) for item in version_data.get("traceability", []))),
        tuple(
            (
                step.get("action"),
                (step.get("target") or {}).get("state_code"),
                (step.get("target") or {}).get("element_code"),
                step.get("value"),
                step.get("expected"),
            )
            for step in version_data.get("steps", [])
        ),
    )


class TestCaseRepository(BaseRepository):

    async def next_tc_code(self, project_id: uuid.UUID) -> str:
        # Serialize code allocation within a project until the caller commits;
        # without the lock, concurrent generations collide on the unique code.
        await self.session.execute(
            select(Project.id).where(Project.id == project_id).with_for_update()
        )
        # Codes are "TC-<n>"; take the numeric maximum so deleted cases never
        # cause a reused code, and TC-1000 sorts after TC-999.
        latest = await self.session.scalar(
            select(
                func.max(cast(func.substring(TestCase.tc_code, r"(\d+)$"), Integer))
            ).where(TestCase.project_id == project_id)
        )
        return f"TC-{(latest or 0) + 1:03d}"

    async def create(
        self,
        project_id: uuid.UUID,
        tc_code: str,
        requirement_id: uuid.UUID,
        requirement_version: int,
        application_map_id: uuid.UUID,
        version_data: dict[str, Any],
        credential_ref: str | None = None,
    ) -> tuple[TestCase, TestCaseVersion]:
        test_case = TestCase(
            project_id=project_id,
            tc_code=tc_code,
            requirement_id=requirement_id,
            requirement_version=requirement_version,
            application_map_id=application_map_id,
            credential_ref=credential_ref,
            current_version=1,
            status=TestCaseStatus.DRAFT,
        )
        self.session.add(test_case)
        await self.session.flush()

        version = self._new_version(test_case.id, 1, version_data)
        self.session.add(version)
        await self.session.flush()
        return test_case, version

    @staticmethod
    def _new_version(
        test_case_id: uuid.UUID, number: int, version_data: dict[str, Any]
    ) -> TestCaseVersion:
        return TestCaseVersion(
            test_case_id=test_case_id,
            version=number,
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

    async def append_version(
        self, test_case: TestCase, version_data: dict[str, Any]
    ) -> TestCaseVersion:
        """Record an edit as a new immutable version; the case returns to DRAFT."""
        next_version = test_case.current_version + 1
        version = self._new_version(test_case.id, next_version, version_data)
        self.session.add(version)
        test_case.current_version = next_version
        test_case.status = TestCaseStatus.DRAFT
        await self.supersede_pending_approvals(test_case.id, "Superseded by an edited version")
        await self.session.flush()
        return version

    async def supersede_pending_approvals(self, test_case_id: uuid.UUID, reason: str) -> None:
        await self.session.execute(
            update(Approval)
            .where(
                Approval.target_type == "test_case",
                Approval.target_id == test_case_id,
                Approval.status == ApprovalStatus.PENDING,
            )
            .values(
                status=ApprovalStatus.REJECTED,
                decided_by="system",
                reason=reason,
                decided_at=func.now(),
            )
        )

    async def existing_signatures(
        self, requirement_id: uuid.UUID, application_map_id: uuid.UUID
    ) -> set[tuple[Any, ...]]:
        pairs = await self.list_for_requirement(requirement_id)
        return {
            case_signature({field: getattr(version, field) for field in _VERSION_FIELDS})
            for tc, version in pairs
            if tc.application_map_id == application_map_id
            and tc.status != TestCaseStatus.OUTDATED
        }

    async def mark_outdated(self, requirement_id: uuid.UUID, current_version: int) -> None:
        """Flag cases written against an older requirement version."""
        stale_ids = select(TestCase.id).where(
            TestCase.requirement_id == requirement_id,
            TestCase.requirement_version < current_version,
        )
        await self.session.execute(
            update(Approval)
            .where(
                Approval.target_type == "test_case",
                Approval.target_id.in_(stale_ids),
                Approval.status == ApprovalStatus.PENDING,
            )
            .values(
                status=ApprovalStatus.REJECTED,
                decided_by="system",
                reason="Requirement changed; test case is outdated",
                decided_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(
            update(TestCase)
            .where(
                TestCase.requirement_id == requirement_id,
                TestCase.requirement_version < current_version,
            )
            .values(status=TestCaseStatus.OUTDATED)
            .execution_options(synchronize_session=False)
        )

    async def delete(self, test_case: TestCase) -> None:
        await self.session.execute(
            delete(Approval).where(
                Approval.target_type == "test_case", Approval.target_id == test_case.id
            )
        )
        await self.session.execute(
            delete(TestCaseVersion).where(TestCaseVersion.test_case_id == test_case.id)
        )
        # Core delete: ORM delete would lazy-load `versions`, which async sessions forbid.
        await self.session.execute(delete(TestCase).where(TestCase.id == test_case.id))
        self.session.expunge(test_case)

    async def list_for_project(
        self, project_id: uuid.UUID
    ) -> list[tuple[TestCase, TestCaseVersion]]:
        result = await self.session.execute(
            select(TestCase)
            .where(TestCase.project_id == project_id)
            .options(selectinload(TestCase.versions))
            .order_by(TestCase.created_at)
        )
        return self._current_pairs(list(result.scalars().all()))

    async def get_with_current_version(
        self, test_case_id: uuid.UUID, *, for_update: bool = False
    ) -> tuple[TestCase, TestCaseVersion] | None:
        query = select(TestCase).where(TestCase.id == test_case_id)
        if for_update:
            query = query.with_for_update()
        tc = (await self.session.execute(query)).scalar_one_or_none()
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
            .order_by(TestCase.created_at)
        )
        return self._current_pairs(list(result.scalars().all()))

    @staticmethod
    def _current_pairs(test_cases: list[TestCase]) -> list[tuple[TestCase, TestCaseVersion]]:
        pairs = []
        for tc in test_cases:
            current = next(
                (v for v in tc.versions if v.version == tc.current_version), None
            )
            if current:
                pairs.append((tc, current))
        return pairs
