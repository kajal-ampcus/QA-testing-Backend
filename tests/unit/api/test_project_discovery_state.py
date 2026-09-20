"""Expose discovery availability without requiring clients to probe a missing map."""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from apps.api.routers.v1.projects import get_project
from infra.db.models.project import Project


@pytest.mark.parametrize("has_map", [False, True])
async def test_project_detail_reports_map_availability(has_map):
    project_id = uuid.uuid4()
    session = AsyncMock()
    session.get.return_value = Project(
        id=project_id, name="Test project", application_url=None, credential_ref=None
    )
    session.scalar.return_value = has_map
    result = await get_project(project_id, session)
    assert result.id == project_id
    assert result.has_application_map is has_map
    query = session.scalar.call_args.args[0]
    assert project_id in query.compile().params.values()


async def test_missing_project_does_not_query_map_availability():
    session = AsyncMock()
    session.get.return_value = None
    with pytest.raises(HTTPException) as caught:
        await get_project(uuid.uuid4(), session)
    assert caught.value.status_code == 404
    session.scalar.assert_not_awaited()
