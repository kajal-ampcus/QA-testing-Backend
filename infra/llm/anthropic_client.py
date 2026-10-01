"""
The ONLY module that calls the Claude API directly (docs/PROJECT_STRUCTURE.md
point 8). Forces structured output via tool use rather than parsing free-text
JSON, wrapped in retry-with-backoff and a process-wide concurrency limit
(infra/llm/retry.py, rate_limiter.py) — necessary because this is a
multi-agent system where several agents may call an LLM close together.

CRITICAL: core/agents/test_execution/ must never import this module — enforced
by the `[tool.importlinter]` contract in pyproject.toml (architecture doc
Section 14's zero-LLM-in-the-loop rule).
"""

import os
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message

from infra.llm.base import LLMClient, LLMStructuredResult
from infra.llm.rate_limiter import get_llm_semaphore
from infra.llm.retry import with_retry

_MAX_REPAIR_ATTEMPTS = 2
_MAX_RESPONSE_TOKENS = 16000


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._client = AsyncAnthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
        self._max_retries = int(os.environ.get("LLM_MAX_RETRIES", "3"))

    async def call_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        tool_schema: dict[str, Any],
        tool_name: str,
        max_tokens: int = 2000,
    ) -> LLMStructuredResult:
        """A truncated tool call (stop_reason=max_tokens) carries partial input,
        so it is retried with a doubled budget; a response without the tool
        call is retried with an explicit reminder. Both are bounded."""
        budget = max_tokens
        user_content = user_prompt
        input_tokens = output_tokens = 0

        async def _do_call() -> Message:
            async with get_llm_semaphore():
                return await self._client.messages.create(
                    model=self.model,
                    max_tokens=budget,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_content}],
                    tools=[
                        {
                            "name": tool_name,
                            "description": f"Return the structured {tool_name} result. This is the only way to respond.",
                            "input_schema": tool_schema,
                        }
                    ],
                    tool_choice={"type": "tool", "name": tool_name},
                )

        for attempt in range(_MAX_REPAIR_ATTEMPTS + 1):
            response = await with_retry(_do_call, max_retries=self._max_retries)
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            last_attempt = attempt == _MAX_REPAIR_ATTEMPTS

            if response.stop_reason == "max_tokens":
                if last_attempt or budget >= _MAX_RESPONSE_TOKENS:
                    raise RuntimeError(
                        f"Model '{self.model}' exhausted the {budget}-token response budget "
                        f"(stop_reason=max_tokens) while generating '{tool_name}'. "
                        "Increase max_tokens in the agent call or reduce the batch size."
                    )
                budget = min(budget * 2, _MAX_RESPONSE_TOKENS)
                user_content = (
                    f"{user_prompt}\n\nYour previous {tool_name} call was cut off at the token "
                    "limit. If everything does not fit, return fewer items, but every item "
                    "must be complete."
                )
                continue

            for block in response.content:
                if block.type == "tool_use" and block.name == tool_name:
                    return LLMStructuredResult(
                        data=block.input,
                        model=self.model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

            if last_attempt:
                break
            user_content = (
                f"{user_prompt}\n\nYour previous response did not call {tool_name}. "
                "Do not answer in prose; call that tool exactly once."
            )

        raise RuntimeError(
            f"Model did not return a '{tool_name}' tool call — cannot proceed without structured output."
        )
