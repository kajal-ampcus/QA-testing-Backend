"""
arq worker entrypoint. Same core/ as apps/api — this process actually runs
Discovery crawls and (later, Milestone 6) Execution runs, which can take
minutes and don't belong inside an HTTP request/response cycle
(docs/PROJECT_STRUCTURE.md point 2).

Run with: arq apps.worker.arq_worker.WorkerSettings
"""

import asyncio
import os
from typing import Any

from arq.connections import RedisSettings

from apps.worker.tasks.run_discovery import run_discovery
from infra.db.session import DATABASE_URL

if os.name == "nt" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    # MCP's stdio transport uses its Popen fallback on the selector loop.
    # The default Windows proactor loop can fail creating its named pipe.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


async def startup(ctx: dict[str, Any]) -> None:
    # Per-worker-process resources go here as later milestones need them
    # (e.g. a shared object-storage client). Nothing needed yet.
    ctx["database_url"] = DATABASE_URL


async def shutdown(ctx: dict[str, Any]) -> None:
    pass


class WorkerSettings:
    functions = [run_discovery]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    max_jobs = 5  # bounds concurrent Discovery crawls — separate from
    # LLM_MAX_CONCURRENT_REQUESTS (infra/llm/rate_limiter.py), which bounds
    # concurrent LLM calls specifically; this bounds concurrent browser
    # sessions, a different, heavier resource.
