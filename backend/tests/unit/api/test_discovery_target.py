"""Discovery uses the saved application URL unless a target is supplied."""

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routers.v1 import application_maps
from infra.db.models.project import Project


class FakeSession:
    async def get(self, model: type[Project], project_id: uuid.UUID) -> Project:
        return Project(
            id=project_id,
            name="ChatGPT",
            application_url="https://chatgpt.com/",
        )


@pytest.mark.asyncio
async def test_discovery_uses_project_url_or_explicit_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued: list[dict[str, Any]] = []

    async def fake_enqueue(function_name: str, project_id: str, payload: dict[str, Any]) -> str:
        queued.append(payload)
        return "job-1"

    monkeypatch.setattr(application_maps, "enqueue", fake_enqueue)
    project_id = uuid.uuid4()
    session = cast(AsyncSession, FakeSession())

    default_response = await application_maps.trigger_discovery(
        project_id, application_maps.DiscoveryTriggerRequest(), session
    )
    explicit_response = await application_maps.trigger_discovery(
        project_id,
        application_maps.DiscoveryTriggerRequest(
            url="https://staging.example.test/", credential_ref="cred:staging_tester"
        ),
        session,
    )

    assert default_response.job_id == explicit_response.job_id == "job-1"
    assert queued[0]["target"]["url"] == "https://chatgpt.com/"
    assert queued[1]["target"]["url"] == "https://staging.example.test/"
    assert queued[1]["target"]["credential_ref"] == "cred:staging_tester"
