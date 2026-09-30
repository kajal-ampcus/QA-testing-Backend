"""Retry-After and 429 backoff for LLM provider rate limits."""

from infra.llm.retry import _retry_delay_seconds


class _RateLimitError(Exception):
    status_code = 429

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.response = type("Response", (), {"headers": headers or {}})()


def test_retry_after_header_wins() -> None:
    assert _retry_delay_seconds(_RateLimitError({"retry-after": "8"}), 0, 1.0) == 8.0


def test_429_without_header_uses_minimum_backoff() -> None:
    assert _retry_delay_seconds(_RateLimitError(), 0, 1.0) == 5.0
