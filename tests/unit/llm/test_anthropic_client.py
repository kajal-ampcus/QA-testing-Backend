"""Anthropic structured calls recover from truncation and missing tool calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from infra.llm.anthropic_client import AnthropicClient


def message(stop_reason: str = "tool_use", tool: bool = True) -> SimpleNamespace:
    content = (
        [SimpleNamespace(type="tool_use", name="extract", input={"ok": True})]
        if tool
        else [SimpleNamespace(type="text", text="Here is the answer")]
    )
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


@pytest.fixture
def client() -> AnthropicClient:
    llm = AnthropicClient(api_key="test", model="claude-test")
    llm._max_retries = 0
    return llm


async def call(client: AnthropicClient):
    return await client.call_structured("system", "user", {"type": "object"}, "extract", 1000)


@pytest.mark.asyncio
async def test_truncated_call_retries_with_larger_budget(client: AnthropicClient) -> None:
    create = AsyncMock(side_effect=[message("max_tokens"), message()])
    client._client.messages.create = create  # type: ignore[method-assign]
    result = await call(client)
    assert result.data == {"ok": True}
    assert [c.kwargs["max_tokens"] for c in create.call_args_list] == [1000, 2000]
    assert result.input_tokens == 20 and result.output_tokens == 10


@pytest.mark.asyncio
async def test_truncation_retries_are_bounded(client: AnthropicClient) -> None:
    client._client.messages.create = AsyncMock(return_value=message("max_tokens"))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="stop_reason=max_tokens"):
        await call(client)


@pytest.mark.asyncio
async def test_prose_reply_is_retried_with_reminder(client: AnthropicClient) -> None:
    create = AsyncMock(side_effect=[message("end_turn", tool=False), message()])
    client._client.messages.create = create  # type: ignore[method-assign]
    assert (await call(client)).data == {"ok": True}
    assert "did not call extract" in create.call_args.kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_missing_tool_call_eventually_fails(client: AnthropicClient) -> None:
    client._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=message("end_turn", tool=False)
    )
    with pytest.raises(RuntimeError, match="did not return"):
        await call(client)
