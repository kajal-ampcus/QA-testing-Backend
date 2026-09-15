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

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from infra.llm.base import LLMClient, LLMStructuredResult
from infra.llm.rate_limiter import get_llm_semaphore
from infra.llm.retry import with_retry


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
        async def _do_call() -> ChatCompletion:
            async with get_llm_semaphore():
                return await self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
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

        response = await with_retry(_do_call, max_retries=self._max_retries)

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
