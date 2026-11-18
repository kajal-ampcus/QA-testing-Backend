from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from domain.enums import TestCaseStatus
from infra.db.repositories.test_case_repo import TestCaseRepository as CaseRepository


def repository(include_current):
    requirement, previous_map, current_map = uuid4(), uuid4(), uuid4()
    data = dict(title="Login UI", objective="Verify login UI", preconditions=["Open login"],
                steps=[{"action": "navigate", "target": {"state_code": "S1"}}],
                expected_result="Login UI is visible", test_data={}, traceability=["AC-1"], category="POSITIVE")
    version = SimpleNamespace(id=uuid4(), **data)
    previous = SimpleNamespace(id=uuid4(), application_map_id=previous_map, status=TestCaseStatus.DRAFT, requirement_version=2)
    current = SimpleNamespace(id=uuid4(), application_map_id=current_map, status=TestCaseStatus.DRAFT, requirement_version=2)
    repo = CaseRepository(SimpleNamespace(scalar=AsyncMock(return_value=2)))
    repo.list_for_requirement = AsyncMock(return_value=[(previous, version)] + ([(current, version)] if include_current else []))
    context = [{"state_code": "S1", "url_pattern": "/login", "reached_via": [], "elements": []}]
    repo._map_contexts = AsyncMock(return_value={previous_map: context, current_map: context})
    return repo, requirement, previous_map, current_map, current, data


@pytest.mark.asyncio
async def test_earlier_map_does_not_block_generation_on_new_map():
    repo, requirement, previous_map, current_map, _, _ = repository(False)
    previous_signatures = await repo.existing_signatures(requirement, previous_map)
    assert len(previous_signatures) == 1
    assert not await repo.existing_signatures(requirement, current_map)
    assert await repo.find_by_signature(requirement, next(iter(previous_signatures)), current_map) is None


@pytest.mark.asyncio
async def test_same_map_duplicates_are_still_found():
    repo, requirement, _, current_map, current, _ = repository(True)
    signatures = await repo.existing_signatures(requirement, current_map)
    assert len(signatures) == 1
    case, _ = await repo.find_by_signature(requirement, next(iter(signatures)), current_map)
    assert case.id == current.id


@pytest.mark.asyncio
async def test_semantic_deduplication_uses_current_map_candidates(monkeypatch):
    repo, requirement, previous_map, current_map, current, data = repository(True)
    matcher = AsyncMock(return_value=str(current.id))
    monkeypatch.setattr("domain.semantic_duplicates.find_semantic_duplicate", matcher)
    match = await repo.find_semantic_duplicate(requirement, data, current_map)
    assert match[0].id == current.id
    assert [candidate["id"] for candidate in matcher.call_args.args[1]] == [str(current.id)]
    # The cache must remain usable when the same repository checks another map.
    await repo.find_semantic_duplicate(requirement, data, previous_map)
    assert str(current.id) not in [candidate["id"] for candidate in matcher.call_args.args[1]]
