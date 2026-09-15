"""
Bounds concurrent in-flight LLM calls across the whole process. This matters
specifically because this is a MULTI-agent system — Application Discovery's
crawl loop alone can want to fire off many LLM calls in quick succession, and
several agents could be active on different projects at once. Without a cap,
a free-tier rate limit (e.g. Groq's ~30 req/min) gets blown through in
seconds, and the retry logic in retry.py just turns that into a slow failure
instead of preventing it.

One process-wide semaphore, sized from LLM_MAX_CONCURRENT_REQUESTS (.env).
Not per-client — the limit is on the underlying provider's rate limit, which
is account-wide, not per-client-instance.
"""

import asyncio
import os

_semaphore: asyncio.Semaphore | None = None


def get_llm_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        max_concurrent = int(os.environ.get("LLM_MAX_CONCURRENT_REQUESTS", "3"))
        _semaphore = asyncio.Semaphore(max_concurrent)
    return _semaphore
