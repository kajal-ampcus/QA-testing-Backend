from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from apps.api.routers.v1 import test_cases as endpoints
from domain.enums import AutomationReviewStatus, TestCaseStatus
from infra.db.repositories.test_case_repo import TestCaseRepository as CaseRepository


@pytest.mark.asyncio
async def test_scoped_regeneration_keeps_unselected_cases():
    login, orders = uuid4(), uuid4()
    rows = [
        (SimpleNamespace(id=login), SimpleNamespace(steps=[{"target": {"state_code": "LOGIN"}}])),
        (SimpleNamespace(id=orders), SimpleNamespace(steps=[{"target": {"state_code": "ORDERS"}}])),
    ]
    session = SimpleNamespace(execute=AsyncMock(return_value=Mock(all=lambda: rows)))
    repo = CaseRepository(session)
    repo._retire_cases = AsyncMock()
    await repo.supersede_for_regeneration(uuid4(), uuid4(), state_codes={"ORDERS"})
    assert repo._retire_cases.call_args.args[0] == [orders]
    await repo.supersede_for_regeneration(uuid4(), uuid4())
    assert repo._retire_cases.call_args.args[0] == [login, orders]


@pytest.mark.asyncio
async def test_retirement_blocks_previous_automation_and_preserves_versions(monkeypatch):
    from infra.db.repositories.automation_repo import AutomationRepository

    script = SimpleNamespace(review_status=AutomationReviewStatus.APPROVED)
    result = Mock()
    result.scalars.return_value.all.return_value = [script]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    append = Mock()
    monkeypatch.setattr(AutomationRepository, "append_version", append)
    await CaseRepository(session)._retire_cases([uuid4()], "Replaced")
    append.assert_called_once_with(script, AutomationReviewStatus.BLOCKED)
    # No destructive SQL: historical approvals and execution references survive.
    assert all(not str(call.args[0]).startswith("DELETE") for call in session.execute.call_args_list)


@pytest.mark.asyncio
async def test_approved_case_can_be_deleted_from_active_set(monkeypatch):
    case = SimpleNamespace(id=uuid4(), status=TestCaseStatus.APPROVED)
    repo = SimpleNamespace(get_with_current_version=AsyncMock(return_value=(case, None)), delete=AsyncMock())
    monkeypatch.setattr(endpoints, "TestCaseRepository", lambda _: repo)
    session = SimpleNamespace(commit=AsyncMock())
    response = await endpoints.delete_test_case(case.id, session)
    assert response.status_code == 204
    repo.delete.assert_awaited_once_with(case)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_archives_approved_case():
    case = SimpleNamespace(id=uuid4(), status=TestCaseStatus.APPROVED)
    repo = CaseRepository(None)
    repo._retire_cases = AsyncMock()
    await repo.delete(case)
    assert case.status == TestCaseStatus.OUTDATED
    assert repo._retire_cases.call_args.args[0] == [case.id]
