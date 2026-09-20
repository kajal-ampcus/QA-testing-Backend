"""Provider selection keeps Gemini credentials and routing isolated."""

from unittest.mock import patch

import pytest

from infra.llm import factory


@pytest.fixture(autouse=True)
def clean_llm_environment(monkeypatch):
    for name in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(factory, "load_dotenv", lambda path: None)


@pytest.mark.parametrize("provider", ["google", "gemini"])
@pytest.mark.parametrize("key_name", ["GEMINI_API_KEY", "GOOGLE_API_KEY", "LLM_API_KEY"])
def test_gemini_provider_and_key_aliases(monkeypatch, provider, key_name):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv(key_name, "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.x.ai/v1")
    with patch.object(factory, "OpenAICompatibleClient") as client:
        assert factory.get_llm_client() is client.return_value
        client.assert_called_once_with(
            api_key="test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            model="gemini-2.5-flash",
        )


def test_gemini_prefers_dedicated_key_and_respects_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    monkeypatch.setenv("LLM_API_KEY", "other-provider-key")
    monkeypatch.setenv("LLM_MODEL", "selected-gemini-model")
    with patch.object(factory, "OpenAICompatibleClient") as client:
        factory.get_llm_client()
        assert client.call_args.kwargs["api_key"] == "gemini-key"
        assert client.call_args.kwargs["model"] == "selected-gemini-model"


def test_gemini_requires_credentials(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google")
    with pytest.raises(RuntimeError, match="requires GEMINI_API_KEY"):
        factory.get_llm_client()


@pytest.mark.parametrize(
    "base_url,model",
    [
        ("https://api.x.ai/v1", "grok-4"),
        ("https://api.groq.com/openai/v1", "llama-3.1-8b-instant"),
    ],
)
def test_existing_compatible_providers(monkeypatch, base_url, model):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", base_url)
    monkeypatch.setenv("LLM_MODEL", model)
    monkeypatch.setenv("LLM_API_KEY", "existing-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    with patch.object(factory, "OpenAICompatibleClient") as client:
        factory.get_llm_client()
        client.assert_called_once_with(api_key="existing-key", base_url=base_url, model=model)


def test_anthropic_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    with patch.object(factory, "AnthropicClient") as client:
        factory.get_llm_client()
        client.assert_called_once_with(api_key="anthropic-key", model="claude-sonnet-5")
