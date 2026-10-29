"""
TestCase repository. Handles append-only test_case_versions writes and
TC-code allocation. Does not commit internally — caller owns the transaction.
"""

import uuid
from typing import Any

from sqlalchemy import Integer, cast, func, select, update
from sqlalchemy.orm import selectinload

from domain.enums import ApprovalStatus, TestCaseStatus
from domain.test_case_identity import case_signature as case_signature
from domain.test_case_identity import discovery_account_context
from infra.db.models.application_map import ApplicationMap, ApplicationMapState
from infra.db.models.approval import Approval
from infra.db.models.project import Project
from infra.db.models.requirement import Requirement
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



class TestCaseRepository(BaseRepository):

    async def find_semantic_duplicate(self, requirement_id: uuid.UUID, version_data: dict):
        from domain.semantic_duplicates import find_semantic_duplicate
        cache = getattr(self, "_duplicate_pairs", None)
        if cache is None:
            self._duplicate_pairs = cache = {}
        if requirement_id not in cache:
            pairs = await self.list_for_requirement(requirement_id)
            current = await self.session.scalar(select(Requirement.current_version).where(Requirement.id == requirement_id))
            cache[requirement_id] = [(tc, v) for tc, v in pairs if tc.status != TestCaseStatus.OUTDATED
                                    and tc.requirement_version == current]
        pairs = cache[requirement_id]
        def summary(data):
            return {key: data.get(key) for key in ("title", "objective", "expected_result", "preconditions", "steps")}
        candidates = [{"id": str(tc.id), **summary({field: getattr(v, field) for field in _VERSION_FIELDS})} for tc, v in pairs]
        found = await find_semantic_duplicate(summary(version_data), candidates)
        return next(((tc, v) for tc, v in pairs if str(tc.id) == found), None)

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
        cache = getattr(self, "_duplicate_pairs", {})
        if requirement_id in cache:
            cache[requirement_id].append((test_case, version))
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
        self, requirement_id: uuid.UUID, application_map_id: uuid.UUID | None = None
    ) -> set[tuple[Any, ...]]:
        """Signatures for the whole requirement.

        The map filter is dropped on purpose: a case regenerated against a new
        map version is the same scenario, not a new one.
        """
        pairs = await self.list_for_requirement(requirement_id)
        current_version = await self.session.scalar(select(Requirement.current_version).where(Requirement.id == requirement_id))
        contexts = await self._map_contexts(pairs)
        return {
            case_signature({field: getattr(version, field) for field in _VERSION_FIELDS}, contexts.get(tc.application_map_id))
            for tc, version in pairs
            if tc.status not in {TestCaseStatus.OUTDATED, TestCaseStatus.REJECTED}
            and (current_version is None or tc.requirement_version == current_version)
        }

    async def find_by_signature(
        self, requirement_id: uuid.UUID, signature: tuple[Any, ...]
    ) -> tuple[TestCase, TestCaseVersion] | None:
        """Locate the saved case that already expresses this scenario."""
        pairs = await self.list_for_requirement(requirement_id)
        current_version = await self.session.scalar(select(Requirement.current_version).where(Requirement.id == requirement_id))
        contexts = await self._map_contexts(pairs)
        for tc, version in pairs:
            if tc.status in {TestCaseStatus.OUTDATED, TestCaseStatus.REJECTED}:
                continue
            if current_version is not None and tc.requirement_version != current_version:
                continue
            data = {field: getattr(version, field) for field in _VERSION_FIELDS}
            if case_signature(data, contexts.get(tc.application_map_id)) == signature:
                return tc, version
        return None

    async def lock_generation(self, requirement_id: uuid.UUID) -> int | None:
        """Serialize signature-check + insert across independent generation runs."""
        result = await self.session.execute(select(Requirement.current_version).where(Requirement.id == requirement_id).with_for_update())
        return result.scalar_one_or_none()

    async def generation_contexts(self, requirement_id: uuid.UUID) -> list[tuple[Any, Any, list[dict[str, Any]]]]:
        pairs = await self.list_for_requirement(requirement_id)
        contexts = await self._map_contexts(pairs)
        return [(case, version, contexts.get(case.application_map_id, [])) for case, version in pairs]

    async def _map_contexts(self, pairs: list) -> dict[uuid.UUID, list[dict[str, Any]]]:
        ids = {case.application_map_id for case, _ in pairs if case.application_map_id}
        if not ids:
            return {}
        result = await self.session.execute(select(ApplicationMapState, ApplicationMap).join(
            ApplicationMap, ApplicationMap.id == ApplicationMapState.application_map_id
        ).where(ApplicationMapState.application_map_id.in_(ids)))
        contexts: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for state, app_map in result.all():
            nodes = (app_map.coverage or {}).get("app_flow_graph", {}).get("nodes", [])
            account_context = discovery_account_context(state.fingerprint, state.reached_via, nodes)
            contexts.setdefault(state.application_map_id, []).append({
                "state_code": state.state_code, "url_pattern": state.url_pattern,
                "reached_via": state.reached_via, "elements": state.elements, "account_context": account_context})
        return contexts

    async def merge_traceability(
        self, test_case_id: uuid.UUID, version_id: uuid.UUID, extra_acs: list[str]
    ) -> None:
        """Attach additional acceptance criteria to an existing case."""
        version = await self.session.get(TestCaseVersion, version_id)
        if version is None:
            return
        merged = sorted(set(list(version.traceability or []) + [a for a in extra_acs if a]))
        if merged != list(version.traceability or []):
            version.traceability = merged
            await self.session.flush()

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
            .where(TestCase.requirement_id == requirement_id, TestCase.requirement_version < current_version)
            .values(status=TestCaseStatus.OUTDATED)
            .execution_options(synchronize_session=False)
        )

    async def _retire_cases(self, ids: list[uuid.UUID], reason: str) -> None:
        if not ids:
            return
        from domain.enums import AutomationReviewStatus
        from infra.db.models.automation import AutomationScript
        from infra.db.repositories.automation_repo import AutomationRepository

        await self.session.execute(
            update(Approval).where(
                Approval.target_type == "test_case", Approval.target_id.in_(ids),
                Approval.status == ApprovalStatus.PENDING,
            ).values(status=ApprovalStatus.REJECTED, decided_by="system",
                     reason=reason, decided_at=func.now())
            .execution_options(synchronize_session=False)
        )
        # Preserve executed evidence and approval history; prevent old scripts
        # from being eligible for subsequent execution.
        scripts = (await self.session.execute(
            select(AutomationScript).where(AutomationScript.test_case_id.in_(ids))
        )).scalars().all()
        automation = AutomationRepository(self.session)
        for script in scripts:
            if script.review_status != AutomationReviewStatus.BLOCKED:
                automation.append_version(script, AutomationReviewStatus.BLOCKED)
        await self.session.execute(
            update(TestCase)
            .where(TestCase.id.in_(ids))
            .values(status=TestCaseStatus.OUTDATED)
            .execution_options(synchronize_session=False)
        )

    async def supersede_for_regeneration(
        self, requirement_id: uuid.UUID, application_map_id: uuid.UUID,
        state_codes: set[str] | None = None,
    ) -> None:
        """Retire only cases touching the selected states, retaining audit history."""
        rows = (await self.session.execute(
            select(TestCase, TestCaseVersion).join(TestCaseVersion,
                (TestCaseVersion.test_case_id == TestCase.id) &
                (TestCaseVersion.version == TestCase.current_version))
            .where(TestCase.requirement_id == requirement_id,
                   TestCase.application_map_id == application_map_id,
                   TestCase.status != TestCaseStatus.OUTDATED)
        )).all()
        ids = [case.id for case, version in rows
               if state_codes is None or any(
                   (step.get("target") or {}).get("state_code") in state_codes
                   for step in (version.steps or [])
               )]
        await self._retire_cases(ids, "Superseded by explicit test-case regeneration")

    async def delete(self, test_case: TestCase) -> None:
        """Remove from active cases without breaking automation/evidence references."""
        await self._retire_cases([test_case.id], "Test case deleted by user")
        test_case.status = TestCaseStatus.OUTDATED

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
