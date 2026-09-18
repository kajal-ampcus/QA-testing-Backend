"""
Retry with exponential backoff for LLM API calls. Only retries on rate-limit
(429) and transient server (5xx) errors — anything else (bad request, auth
failure, model doesn't support tool calling) fails immediately, since
retrying those just wastes calls and hides a real configuration problem
behind a slow, confusing timeout.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _status_code(exc: Exception) -> int | None:
    """Both the anthropic and openai SDKs expose .status_code on their
    APIStatusError subclasses — duck-typed here so this stays SDK-agnostic
    rather than importing both SDKs' exception hierarchies."""
    return getattr(exc, "status_code", None)


async def with_retry[T](
    call: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    base_delay_seconds: float = 1.0,
) -> T:
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await call()
        except Exception as exc:  # noqa: BLE001 — intentionally broad, filtered below
            status = _status_code(exc)
            if status not in RETRYABLE_STATUS_CODES or attempt == max_retries:
                raise
            last_exc = exc
            delay = base_delay_seconds * (2**attempt)
            logger.warning(
                "LLM call failed with status %s (attempt %d/%d) — retrying in %.1fs",
                status,
                attempt + 1,
                max_retries,
                delay,
            )
            await asyncio.sleep(delay)

    # Unreachable in practice (the loop always returns or raises), but keeps
    # the type checker honest and gives a clear error if that assumption
    # is ever wrong.
    raise RuntimeError("with_retry exhausted attempts without returning or raising") from last_exc
