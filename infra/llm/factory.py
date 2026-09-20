"""
The ONE place that decides which LLM provider/model to use, read from .env.
No agent, anywhere, ever hardcodes a provider — they all call get_llm_client()
and depend only on the LLMClient interface (infra/llm/base.py). Switching
models or providers is always a .env edit, never a code change.

.env variables:

  LLM_PROVIDER   "anthropic" | "openai_compatible" | "google" | "gemini"
  LLM_MODEL      provider-specific model id           (has a sane default per provider)

  # when LLM_PROVIDER=anthropic:
  ANTHROPIC_API_KEY

  # when LLM_PROVIDER=openai_compatible (OpenAI, Groq, xAI/Grok, Ollama, ...):
  LLM_BASE_URL   e.g. https://api.groq.com/openai/v1
  LLM_API_KEY    (not required for local Ollama)

  # when LLM_PROVIDER=google (alias: gemini):
  GEMINI_API_KEY or GOOGLE_API_KEY or LLM_API_KEY
  # Uses Google's OpenAI-compatible endpoint; LLM_BASE_URL is not needed.

See .env.example for a ready-to-uncomment block per provider.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from infra.llm.anthropic_client import AnthropicClient
from infra.llm.base import LLMClient
from infra.llm.openai_compatible_client import OpenAICompatibleClient


def get_llm_client() -> LLMClient:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    model = os.environ.get("LLM_MODEL") or None

    if provider == "anthropic":
        return AnthropicClient(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            model=model or "claude-sonnet-5",
        )

    if provider in {"google", "gemini"}:
        api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("LLM_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                "LLM_PROVIDER=google/gemini requires GEMINI_API_KEY, GOOGLE_API_KEY, "
                "or LLM_API_KEY to be set in .env."
            )
        return OpenAICompatibleClient(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            model=model or "gemini-2.5-flash",
        )

    if provider == "openai_compatible":
        base_url = os.environ.get("LLM_BASE_URL")
        if not base_url:
            raise RuntimeError(
                "LLM_PROVIDER=openai_compatible requires LLM_BASE_URL to be set in .env "
                "(e.g. https://api.groq.com/openai/v1 for Groq, https://api.openai.com/v1 "
                "for OpenAI, https://api.x.ai/v1 for Grok, http://localhost:11434/v1 for Ollama)."
            )
        return OpenAICompatibleClient(
            api_key=os.environ.get("LLM_API_KEY"),
            base_url=base_url,
            model=model or "llama-3.1-8b-instant",
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER: {provider!r} "
        "(expected 'anthropic', 'openai_compatible', 'google', or 'gemini')"
    )
