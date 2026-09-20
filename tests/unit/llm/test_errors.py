"""Configuration failures must not be misreported as malformed model output."""

import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from openai import BadRequestError

from apps.api.routers.v1 import requirements
from infra.llm.errors import requirement_failure_detail
from infra.llm.openai_compatible_client import OpenAICompatibleClient


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, "same provider"),
        (401, "authentication"),
        (403, "authentication"),
        (429, "quota"),
        (503, "temporarily unavailable"),
    ],
)
def test_errors_are_actionable_without_echoing_provider_payloads(status, expected):
    class ProviderError(Exception):
        status_code = status

    error = ProviderError("sensitive API key or requirement text")
    assert expected in requirement_failure_detail(error)
    assert "sensitive" not in requirement_failure_detail(error)


async def test_bad_configuration_is_not_repaired_as_schema_output():
    client = OpenAICompatibleClient(
        api_key="test", base_url="https://example.test/v1", model="test-model"
    )
    error = BadRequestError(
        "Invalid API key for provider",
        response=httpx.Response(400, request=httpx.Request("POST", "https://example.test")),
        body={"error": "invalid API key"},
    )
    client._client.chat.completions.create = AsyncMock(side_effect=error)
    try:
        with pytest.raises(BadRequestError):
            await client.call_structured("system", "user", {"type": "object"}, "extract")
        client._client.chat.completions.create.assert_awaited_once()
    finally:
        await client._client.close()


async def test_agent_initialization_failure_is_audited(monkeypatch):
    def fail(self):
        raise ValueError("Invalid configuration containing a secret")

    monkeypatch.setattr(requirements.RequirementUnderstandingAgent, "__init__", fail)
    session = AsyncMock()
    session.add = lambda row: None
    with pytest.raises(HTTPException) as caught:
        await requirements._extract_requirement(session, uuid.uuid4(), "test requirement")
    assert caught.value.status_code == 502
    assert "secret" not in caught.value.detail
    session.commit.assert_awaited_once()
