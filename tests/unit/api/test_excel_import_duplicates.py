from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from apps.api.routers.v1 import test_cases as api
from domain.enums import RequirementStatus, TestCaseStatus


@pytest.mark.asyncio
@pytest.mark.parametrize("detailed_steps", [False, True])
@pytest.mark.parametrize("manual_entry", [False, True])
async def test_reimport_returns_existing_without_inserting(monkeypatch, detailed_steps, manual_entry):
    project_id, requirement_id, map_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    body = api.CreateTestCaseRequest(
        requirement_id=requirement_id, application_map_id=map_id,
        title="Login works", objective="Verify UI: Login works",
        expected_result="Dashboard opens",
        category="POSITIVE", skip_existing=True, manual_entry=manual_entry,
    )
    requirement = SimpleNamespace(id=requirement_id, project_id=project_id,
                                  status=RequirementStatus.APPROVED, current_version=1)
    saved = SimpleNamespace(title=" LOGIN  works ", expected_result=body.expected_result,
                            category=body.category, preconditions=[],
                            steps=api._manual_steps(state_code="OLD", expected_result=body.expected_result, step_notes=[]))
    existing = SimpleNamespace(status=TestCaseStatus.APPROVED, requirement_version=1)
    if manual_entry:
        saved.expected_result = "Different wording of the expected result"
        saved.category = "EDGE_CASE"
    if detailed_steps:
        saved.preconditions = ["User is on the login page"]
        saved.steps = [{"action": "click", "target": {"state_code": "OLD", "element_code": "EL-1"}, "expected": None}]
    repo = SimpleNamespace(lock_generation=AsyncMock(),
                           list_for_requirement=AsyncMock(return_value=[(existing, saved)]),
                           create=AsyncMock(), next_tc_code=AsyncMock())
    monkeypatch.setattr(api, "TestCaseRepository", lambda db: repo)
    monkeypatch.setattr(api, "RequirementRepository", lambda db: SimpleNamespace(
        get_with_current_version=AsyncMock(return_value=(requirement, SimpleNamespace(acceptance_criteria=[{"id": "AC-1"}])))))
    monkeypatch.setattr(api, "ApplicationMapRepository", lambda db: SimpleNamespace(
        get_with_states=AsyncMock(return_value=SimpleNamespace(id=map_id, project_id=project_id, states=[SimpleNamespace(state_code="NEW")]))))
    monkeypatch.setattr(api, "_to_out", lambda *args: SimpleNamespace(model_copy=lambda update: update))
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace()), commit=AsyncMock())
    assert await api.create_test_case(project_id, body, db) == {"already_exists": True}
    repo.lock_generation.assert_awaited_once_with(requirement_id)
    repo.create.assert_not_awaited()
    repo.next_tc_code.assert_not_awaited()
    db.commit.assert_awaited_once()


def test_import_identity_preserves_distinct_scenarios():
    def identity(title="Login works", expected="Dashboard opens", notes=None):
        return api._import_identity(title, expected, "POSITIVE", [], api._manual_steps(
            state_code="STATE-1", expected_result=expected, step_notes=notes or []))
    assert identity() == identity(title="  LOGIN   works ")
    assert identity() != identity(title="Logout works")
    assert identity() != identity(expected="Error appears")
    assert identity(notes=["Use admin account"]) != identity(notes=["Use reader account"])


def test_legacy_import_normalizes_ui_before_length_validation():
    request = api.CreateTestCaseRequest(
        requirement_id=uuid.uuid4(), title="Login works", objective="UI",
        expected_result="Dashboard opens", traceability=["AC-1"],
    )
    assert request.objective == "Verify UI: Login works"
    assert request.skip_existing is True


def test_manual_case_can_omit_acceptance_criteria():
    request = api.CreateTestCaseRequest(requirement_id=uuid.uuid4(), title="Login validation",
        objective="Verify empty password handling", expected_result="Required password message appears")
    assert request.traceability == []
    spec = api.ManualTestCaseSpec(title=request.title, objective=request.objective,
        expected_result=request.expected_result, category=request.category, preconditions=[],
        steps=api._manual_steps(state_code="STATE-001", expected_result=request.expected_result, step_notes=[]),
        confidence=1.0)
    assert spec.traceability == []

