"""
Abstract interface every LLM provider client implements.

LLMClient.call_structured returns an LLMStructuredResult, not a bare dict —
token usage travels with every call so cost/rate tracking is possible system-
wide without each agent re-deriving it. This matters specifically because
this is a MULTI-agent system: 10 agents potentially calling an LLM, on a free
tier with real per-minute limits (e.g. Groq's free tier: ~30 requests/min) —
without usage visibility, a runaway crawl or a retry storm is invisible until
it's already burned the budget or triggered a hard rate-limit failure.
"""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class LLMStructuredResult(BaseModel):
    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMClient(ABC):
    @abstractmethod
    async def call_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        tool_schema: dict[str, Any],
        tool_name: str,
        max_tokens: int = 2000,
    ) -> LLMStructuredResult:
        """Forces the model to respond via a single structured tool/function
        call matching tool_schema. Implementations must raise rather than
        silently falling back to parsing prose if the model doesn't use the
        tool (architecture doc Section 30) — and must raise a distinguishable
        error on rate-limit responses so infra/llm/retry.py can back off
        correctly rather than treating it as a permanent failure."""
        ...
