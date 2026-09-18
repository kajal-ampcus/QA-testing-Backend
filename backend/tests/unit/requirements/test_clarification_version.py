"""Tester clarifications preserve the old requirement version and its evidence."""

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from infra.db.models.requirement import Requirement, RequirementVersion
from infra.db.repositories.requirement_repo import RequirementRepository


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        pass


@pytest.mark.asyncio
async def test_clarification_appends_version_with_explicit_tester_evidence() -> None:
    requirement_id = uuid.uuid4()
    requirement = Requirement(id=requirement_id, project_id=uuid.uuid4(), req_code="REQ-001")
    requirement.current_version = 3
    old_ambiguities: list[dict[str, Any]] = [
        {"field": "save timing", "issue": "When is a chat saved?", "requires_clarification": True}
    ]
    old_criteria = [{"id": "AC-1", "text": "Chat history is available", "source": "REQUIREMENT"}]
    current = RequirementVersion(
        requirement_id=requirement_id,
        version=3,
        raw_text="Users can view chat history.",
        title="Chat history",
        description="Users can view chat history.",
        acceptance_criteria=old_criteria,
        ambiguities=old_ambiguities,
        domain_tags=["chat"],
    )
    session = FakeSession()

    new = await RequirementRepository(cast(AsyncSession, session)).append_clarified_version(
        requirement,
        current,
        [(old_ambiguities[0], "Save after every chatbot reply.")],
        "tester",
    )

    assert requirement.current_version == 4
    assert current.version == 3 and current.ambiguities == old_ambiguities
    assert current.acceptance_criteria == old_criteria
    assert new in session.added and new.version == 4 and new.ambiguities == []
    assert "tester" in new.raw_text and "When is a chat saved?" in new.raw_text
    assert new.acceptance_criteria[-1] == {
        "id": "AC-CLARIFY-4-1",
        "text": "Save after every chatbot reply.",
        "source": "REQUIREMENT",
    }
