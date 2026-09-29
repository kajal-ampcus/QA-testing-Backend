"""Gemini truncation recovery must not change other providers' requests."""

from unittest.mock import AsyncMock

import pytest
from openai.types.chat import ChatCompletion

from infra.llm.gemini_schema import inline_schema_refs
from infra.llm.openai_compatible_client import OpenAICompatibleClient


def completion(finish="tool_calls", arguments='{"ok": true}', choices=True):
    return ChatCompletion.model_validate(
        {
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "generate_test_cases", "arguments": arguments},
                            }
                        ],
                    },
                }
            ]
            if choices
            else [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
    )


@pytest.fixture
async def client():
    instance = OpenAICompatibleClient(
        "test", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash"
    )
    yield instance
    await instance._client.close()


async def call(client):
    return await client.call_structured(
        "system", "user", {"type": "object"}, "generate_test_cases", 4000
    )


async def test_truncated_json_is_retried_and_usage_accumulates(client):
    create = AsyncMock(side_effect=[completion("length", '{"ok":'), completion()])
    client._client.chat.completions.create = create
    result = await call(client)
    assert result.data == {"ok": True}
    assert (result.input_tokens, result.output_tokens) == (20, 40)
    assert [c.kwargs["max_tokens"] for c in create.call_args_list] == [16000, 32000]
    assert all(c.kwargs["reasoning_effort"] == "low" for c in create.call_args_list)


async def test_missing_tool_after_truncation_is_retried(client):
    truncated = completion("length")
    truncated.choices[0].message.tool_calls = None
    create = AsyncMock(side_effect=[truncated, completion()])
    client._client.chat.completions.create = create
    assert (await call(client)).data == {"ok": True}
    assert create.await_count == 2


async def test_gemini_receives_inline_test_case_schema(client):
    from core.agents.test_design.schemas import TestCaseBatch

    schema = TestCaseBatch.model_json_schema()
    create = AsyncMock(return_value=completion())
    client._client.chat.completions.create = create
    await client.call_structured("system", "user", schema, "generate_test_cases")
    parameters = create.call_args.kwargs["tools"][0]["function"]["parameters"]
    assert "$defs" not in parameters
    assert "title" not in parameters
    case = parameters["properties"]["test_cases"]["items"]
    assert "title" in case["properties"]  # Keep the actual test-case title field.
    assert "title" not in case["properties"]["title"]
    step = parameters["properties"]["test_cases"]["items"]["properties"]["steps"]["items"]
    assert "step_number" in step["required"]
    assert "state_code" in step["properties"]["target"]["required"]
    assert "$defs" in schema  # The caller's schema remains unchanged.


def test_recursive_schema_fails_clearly():
    with pytest.raises(ValueError, match="non-recursive"):
        inline_schema_refs({"$defs": {"Node": {"$ref": "#/$defs/Node"}}, "$ref": "#/$defs/Node"})


async def test_token_budget_retries_are_bounded(client):
    create = AsyncMock(return_value=completion("length"))
    client._client.chat.completions.create = create
    with pytest.raises(RuntimeError, match="32000-token.*finish_reason=length"):
        await call(client)
    assert [c.kwargs["max_tokens"] for c in create.call_args_list] == [16000, 32000]


async def test_malformed_gemini_call_is_repaired(client):
    response = completion()
    response.choices[0].finish_reason = "function_call_filter: MALFORMED_FUNCTION_CALL"
    response.choices[0].message.tool_calls = None
    create = AsyncMock(side_effect=[response, completion()])
    client._client.chat.completions.create = create
    assert (await call(client)).data == {"ok": True}
    assert create.await_count == 2
    assert (
        "Nested objects must be JSON values" in create.call_args.kwargs["messages"][-1]["content"]
    )


async def test_malformed_gemini_repair_is_bounded(client):
    response = completion()
    response.choices[0].finish_reason = "function_call_filter: MALFORMED_FUNCTION_CALL"
    response.choices[0].message.tool_calls = None
    create = AsyncMock(return_value=response)
    client._client.chat.completions.create = create
    with pytest.raises(RuntimeError, match="MALFORMED_FUNCTION_CALL.*after 2 repair attempts"):
        await call(client)
    assert create.await_count == 3


async def test_no_choices_has_clear_error(client):
    client._client.chat.completions.create = AsyncMock(return_value=completion(choices=False))
    with pytest.raises(RuntimeError, match="returned no choices"):
        await call(client)


async def test_blocked_output_is_not_retried(client):
    response = completion("content_filter")
    response.choices[0].message.tool_calls = None
    create = AsyncMock(return_value=response)
    client._client.chat.completions.create = create
    with pytest.raises(RuntimeError, match="finish_reason=content_filter"):
        await call(client)
    create.assert_awaited_once()


async def test_missing_forced_tool_call_is_retried(client):
    response = completion("stop")
    response.choices[0].message.tool_calls = None
    response.choices[0].message.content = "Here is the requested result."
    create = AsyncMock(side_effect=[response, completion()])
    client._client.chat.completions.create = create

    assert (await call(client)).data == {"ok": True}
    assert create.await_count == 2
    assert "did not call the required" in create.call_args.kwargs["messages"][-1]["content"]


async def test_missing_forced_tool_call_repair_is_bounded(client):
    response = completion("stop")
    response.choices[0].message.tool_calls = None
    response.choices[0].message.content = "Unable to call the function."
    create = AsyncMock(return_value=response)
    client._client.chat.completions.create = create

    with pytest.raises(RuntimeError, match="did not return.*function call"):
        await call(client)
    assert create.await_count == 3


@pytest.mark.parametrize("model", ["grok-4", "llama-3.1-8b-instant"])
async def test_other_providers_keep_request_options(model):
    client = OpenAICompatibleClient("test", "https://example.test/v1", model)
    create = AsyncMock(return_value=completion())
    client._client.chat.completions.create = create
    try:
        await call(client)
        assert create.call_args.kwargs["max_tokens"] == 4000
        assert "reasoning_effort" not in create.call_args.kwargs
        create.assert_awaited_once()
    finally:
        await client._client.close()
