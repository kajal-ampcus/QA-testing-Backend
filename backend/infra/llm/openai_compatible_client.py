"""
Generic OpenAI-compatible client — OpenAI, Groq, xAI/Grok, local Ollama, or
any provider speaking the OpenAI chat-completions wire format. Same
retry/concurrency wrapping as anthropic_client.py, and returns real token
usage — every provider in the OpenAI-compatible family reports usage the
same way (response.usage.prompt_tokens / completion_tokens), so this is one
implementation, not one per provider.

NOTE: tool/function calling support varies by provider and model. On Groq's
free tier specifically, Llama 3.1/3.3 models are confirmed to support forced
tool calling; smaller/other free models may not — that shows up as the
RuntimeError below, not a silent bad result.
"""

import json
import os
from typing import Any

from openai import AsyncOpenAI, BadRequestError
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from infra.llm.base import LLMClient, LLMStructuredResult
from infra.llm.rate_limiter import get_llm_semaphore
from infra.llm.retry import with_retry

# Small/free-tier models sometimes ignore the tool JSON schema and return
# their own ad-hoc structure. Some OpenAI-compatible providers (Groq's
# 'tool_use_failed' is the confirmed case) validate the tool call against the
# schema server-side and reject it with a 400 BadRequestError that names
# exactly which required properties were missing — that's a genuine signal
# we can hand straight back to the model, not a real "bad request" from us.
_SCHEMA_ERROR_MARKERS = ("tool_use_failed", "did not match schema", "tool call validation failed")
_PARSE_ERROR_MARKERS = ("failed to parse tool call arguments", "invalid json")


def _is_schema_validation_error(exc: BadRequestError) -> bool:
    return any(marker in str(exc).lower() for marker in _SCHEMA_ERROR_MARKERS)


def _is_json_parse_error(exc: BadRequestError) -> bool:
    return any(marker in str(exc).lower() for marker in _PARSE_ERROR_MARKERS)


class OpenAICompatibleClient(LLMClient):
    def __init__(self, api_key: str | None, base_url: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url)
        self.model = model
        self._max_retries = int(os.environ.get("LLM_MAX_RETRIES", "3"))

    async def call_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        tool_schema: dict[str, Any],
        tool_name: str,
        max_tokens: int = 2000,
    ) -> LLMStructuredResult:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        async def _do_call() -> ChatCompletion:
            async with get_llm_semaphore():
                return await self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=messages,
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "description": f"Return the structured {tool_name} result. This is the only way to respond.",
                                "parameters": tool_schema,
                            },
                        }
                    ],
                    tool_choice={"type": "function", "function": {"name": tool_name}},
                )

        # Bounded repair attempts: if the provider rejects the tool call
        # because it didn't match the schema (or wasn't even valid JSON), feed
        # a corrective message back to the model and ask it to try again —
        # much cheaper and more reliable than failing the whole agent run over
        # one malformed generation. The two failure modes need different
        # messages: a schema mismatch names the exact violations, but a raw
        # JSON parse failure (most often caused by the model sprinkling in
        # // comments, which aren't valid JSON) gives no such detail, so the
        # generic "fix the violations above" message does nothing useful —
        # it has to spell out the likely cause instead.
        max_repair_attempts = 2
        for attempt in range(max_repair_attempts + 1):
            try:
                response = await with_retry(_do_call, max_retries=self._max_retries)
                break
            except BadRequestError as exc:
                if attempt < max_repair_attempts and _is_json_parse_error(exc):
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your last response could not even be parsed as JSON "
                            f"(error: {exc}). This almost always means you included a "
                            "// or /* */ comment, or a trailing comma, somewhere in the "
                            f"arguments. Call {tool_name} again with STRICT, plain JSON — "
                            "zero comments anywhere, no trailing commas. If you need to "
                            "note an assumption or gap, put that sentence inside the "
                            "`objective` field's text, never as a comment token."
                        ),
                    })
                    continue
                if attempt < max_repair_attempts and _is_schema_validation_error(exc):
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your last response did not match the required schema:\n"
                            f"{exc}\n\n"
                            f"Call {tool_name} again and fix EXACTLY the violations listed "
                            "above — missing required properties must be added, and any "
                            "value flagged as invalid must be replaced with one of the "
                            "allowed values from the schema. Use the exact property names "
                            "from the schema, not your own names for the same idea."
                        ),
                    })
                    continue
                raise RuntimeError(
                    f"Model '{self.model}' returned a tool call that didn't match the "
                    f"'{tool_name}' schema, and {max_repair_attempts} repair "
                    f"attempt(s) also failed: {exc}"
                ) from exc
        else:
            raise RuntimeError(
                f"Model '{self.model}' failed to produce a valid '{tool_name}' tool call "
                f"after {max_repair_attempts} repair attempt(s)."
            )

        message = response.choices[0].message
        if message.tool_calls:
            for call in message.tool_calls:
                if call.type == "function" and call.function.name == tool_name:
                    usage = response.usage
                    return LLMStructuredResult(
                        data=json.loads(call.function.arguments),
                        model=self.model,
                        input_tokens=usage.prompt_tokens if usage else 0,
                        output_tokens=usage.completion_tokens if usage else 0,
                    )

        raise RuntimeError(
            f"Model '{self.model}' did not return a '{tool_name}' function call — "
            f"either it doesn't support forced tool calling, or the request failed. "
            f"Check {os.environ.get('LLM_BASE_URL', '(base url not set)')} and try a "
            f"model documented to support tool/function calling."
        )
