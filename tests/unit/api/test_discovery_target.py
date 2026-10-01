"""Discovery uses the saved application URL unless a target is supplied."""

import uuid
from typing import Any, cast

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routers.v1 import application_maps
from infra.db.models.project import Project


class FakeSession:
    def __init__(self, owned_credential: bool = True) -> None:
        self.owned_credential = owned_credential

    async def get(self, model: type[Project], project_id: uuid.UUID) -> Project:
        return Project(
            id=project_id,
            name="ChatGPT",
            application_url="https://chatgpt.com/",
        )

    async def scalar(self, query: object) -> object:
        return uuid.uuid4() if self.owned_credential else None


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []

    async def fake_enqueue(
        function_name: str, project_id: str, payload: dict[str, Any], job_id: str | None = None
    ) -> str:
        payloads.append(payload)
        return job_id or "job-1"

    async def allow_target(url: str) -> None:
        return None

    async def claim(project_id: uuid.UUID) -> str:
        return "job-1"

    monkeypatch.setattr(application_maps, "enqueue", fake_enqueue)
    monkeypatch.setattr(application_maps, "_check_target", allow_target)
    monkeypatch.setattr(application_maps, "_claim_discovery_slot", claim)
    return payloads


@pytest.mark.asyncio
async def test_discovery_uses_project_url_or_explicit_target(
    queued: list[dict[str, Any]],
) -> None:
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


@pytest.mark.asyncio
async def test_discovery_rejects_another_projects_credential(
    queued: list[dict[str, Any]],
) -> None:
    session = cast(AsyncSession, FakeSession(owned_credential=False))

    with pytest.raises(HTTPException) as caught:
        await application_maps.trigger_discovery(
            uuid.uuid4(),
            application_maps.DiscoveryTriggerRequest(credential_ref="cred:other_project"),
            session,
        )

    assert caught.value.status_code == 422
    assert queued == []
