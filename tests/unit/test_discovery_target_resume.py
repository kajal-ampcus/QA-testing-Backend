"""A saved discovery cannot silently override a newly entered target URL."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from apps.api.routers.v1 import application_maps


async def test_resume_rejects_a_different_target(monkeypatch):
    project_id = uuid.uuid4()
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
        application_url="http://host.docker.internal:3003/",
    )))
    repository = SimpleNamespace(get_with_states=AsyncMock(return_value=SimpleNamespace(
        project_id=project_id,
        base_url="http://host.docker.internal:3000/",
    )))
    monkeypatch.setattr(application_maps, "ApplicationMapRepository", lambda _: repository)
    request = application_maps.DiscoveryTriggerRequest(
        url="http://host.docker.internal:3003/",
        resume_application_map_id=uuid.uuid4(),
    )

    with pytest.raises(HTTPException) as caught:
        await application_maps.trigger_discovery(project_id, request, session)

    assert caught.value.status_code == 409
    assert "Start a fresh discovery" in caught.value.detail
