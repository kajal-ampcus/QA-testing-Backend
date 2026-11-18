"""Cross-source uniqueness and transaction-boundary regression coverage."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.api.routers.v1 import test_cases as api
from domain.enums import TestCaseStatus as CaseStatus
from domain.semantic_duplicates import find_semantic_duplicate
from infra.db.repositories.test_case_repo import TestCaseRepository as CaseRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("first,second,outcome", [
    ("Verify login with an incorrect password", "Check sign in using wrong password", "Login is rejected"),
    ("Sign in without entering a password", "Login with a missing password", "Password is required"),
    ("User can log in with correct credentials", "Verify successful sign in with valid credentials", "Dashboard opens"),
    ("Verify user can add a product", "Check user creates the product", "Product is displayed"),
    ("Verify user can remove a product", "Check user deletes the product", "Product is removed"),
])
async def test_paraphrases_match_in_both_source_orders(first, second, outcome):
    left = {"title": first, "expected_result": outcome, "test_data": {"source": "manual"}}
    right = {"title": second, "expected_result": outcome, "test_data": {"source": "excel_import"}}
    assert await find_semantic_duplicate(left, [{"id": "saved", **right}]) == "saved"
    assert await find_semantic_duplicate(right, [{"id": "saved", **left}]) == "saved"


@pytest.mark.asyncio
@pytest.mark.parametrize("left,right", [
    ({"title": "Invalid password after 3 attempts"}, {"title": "Invalid password after 5 attempts"}),
    ({"title": "Invalid password", "test_data": {"username": "alice", "password": "bob"}},
     {"title": "Invalid password", "test_data": {"username": "bob", "password": "alice"}}),
    ({"title": "Login with invalid username and valid password"}, {"title": "Login with empty username and valid password"}),
    ({"title": "Login with invalid username and valid password"}, {"title": "Login with valid username and invalid password"}),
    ({"title": "Create product"}, {"title": "Delete product"}),
])
async def test_distinct_conditions_are_not_discarded(left, right):
    assert await find_semantic_duplicate(left, [{"id": "saved", **right}]) is None


@pytest.mark.asyncio
async def test_requirement_lock_discards_candidates_cached_before_commit():
    requirement_id = uuid4()
    repo = CaseRepository(SimpleNamespace(execute=AsyncMock(return_value=Mock(scalar_one_or_none=lambda: 2))))
    repo._duplicate_pairs = {requirement_id: [("stale", "case")]}
    assert await repo.lock_generation(requirement_id) == 2
    assert requirement_id not in repo._duplicate_pairs


@pytest.mark.asyncio
async def test_matcher_excludes_current_case_and_rejected_cases():
    requirement_id, map_id = uuid4(), uuid4()
    version = SimpleNamespace(title="Login empty password", objective="Verify login", expected_result="Required",
        preconditions=[], steps=[], test_data={}, traceability=[], category="NEGATIVE")
    own = SimpleNamespace(id=uuid4(), application_map_id=map_id, requirement_version=1, status=CaseStatus.DRAFT)
    rejected = SimpleNamespace(id=uuid4(), application_map_id=map_id, requirement_version=1, status=CaseStatus.REJECTED)
    repo = CaseRepository(SimpleNamespace(scalar=AsyncMock(return_value=1)))
    repo.list_for_requirement = AsyncMock(return_value=[(own, version), (rejected, version)])
    assert await repo.find_semantic_duplicate(requirement_id, vars(version), map_id, exclude_case_id=own.id) is None


@pytest.mark.asyncio
async def test_revision_cannot_turn_a_draft_into_an_existing_scenario(monkeypatch):
    case = SimpleNamespace(id=uuid4(), requirement_id=uuid4(), application_map_id=uuid4(),
                           current_version=1, status=CaseStatus.DRAFT)
    repo = SimpleNamespace(get_with_current_version=AsyncMock(return_value=(case, None)),
        lock_generation=AsyncMock(), find_semantic_duplicate=AsyncMock(return_value=(SimpleNamespace(tc_code="TC-001"), None)),
        append_version=AsyncMock())
    requirement = SimpleNamespace(id=case.requirement_id, current_version=1)
    app_map = SimpleNamespace(states=[SimpleNamespace(state_code="S1", url_pattern="/login", elements=[])])
    monkeypatch.setattr(api, "TestCaseRepository", lambda _: repo)
    monkeypatch.setattr(api, "RequirementRepository", lambda _: SimpleNamespace(
        get_with_current_version=AsyncMock(return_value=(requirement, SimpleNamespace(acceptance_criteria=[])))))
    monkeypatch.setattr(api, "ApplicationMapRepository", lambda _: SimpleNamespace(get_with_states=AsyncMock(return_value=app_map)))
    monkeypatch.setattr(api, "TestCaseValidationAgent", lambda: SimpleNamespace(issues_for=lambda *args: []))
    body = api.TestCaseRevisionRequest(expected_version=1, title="Login empty password", objective="Verify login",
        expected_result="Password required", category="NEGATIVE",
        steps=api._manual_steps(state_code="S1", expected_result="Password required", step_notes=[]))
    db = SimpleNamespace(commit=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await api.revise_test_case(case.id, body, db)
    assert exc.value.status_code == 409
    assert "TC-001" in exc.value.detail
    assert repo.find_semantic_duplicate.call_args.kwargs == {"exclude_case_id": case.id}
    repo.append_version.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_saves_only_missing_cases_from_mixed_batch():
    from core.agents.test_design.agent import TestDesignAgent as DesignAgent
    from core.agents.test_design.schemas import TestCaseSpec
    from domain.test_case_identity import case_signature

    def spec(title, expected):
        return TestCaseSpec(title=title, objective="Verify field input", category="POSITIVE",
            preconditions=[], steps=api._manual_steps(state_code="S1", expected_result=expected, step_notes=[]),
            expected_result=expected, test_data={}, traceability=["AC-1"], confidence=1.0)

    imported = spec("Enter a valid username", "Username accepts input")
    existing = SimpleNamespace(id=uuid4(), current_version=1, status=CaseStatus.APPROVED, source="excel_import")
    saved = [(existing, SimpleNamespace(id=uuid4(), **imported.model_dump(mode="json")))]
    async def find(_requirement, data, _map):
        match = await find_semantic_duplicate(data, [{**vars(v), "id": str(tc.id)} for tc, v in saved])
        return next(((tc, v) for tc, v in saved if str(tc.id) == match), None)
    async def create(**kwargs):
        pair = (SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4(), **kwargs["version_data"]))
        saved.append(pair)
        return pair
    async def by_signature(_requirement, signature, _map):
        return next(((tc, v) for tc, v in saved if case_signature(vars(v)) == signature), None)

    repo = SimpleNamespace(find_semantic_duplicate=find, find_by_signature=by_signature,
        next_tc_code=AsyncMock(return_value="TC-002"), create=AsyncMock(side_effect=create),
        merge_traceability=AsyncMock(), session=SimpleNamespace(commit=AsyncMock()))
    agent = DesignAgent(SimpleNamespace(), SimpleNamespace(), repo, llm_client=object())
    missing = spec("Verify password accepts valid input", "Password accepts input")
    paraphrase = spec("Verify username accepts valid input", "Username accepts input")
    artifacts, skipped, _, _ = await agent._persist_specs(project_id=uuid4(), requirement_id=uuid4(),
        requirement_version=1, application_map_id=uuid4(), specs=[paraphrase, missing, missing], known_signatures=set())
    assert len(artifacts) == 1
    assert skipped == 2
    repo.create.assert_awaited_once()
    assert len(saved) == 2
