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

from apps.worker.tasks.run_discovery import run_discovery
from infra.db.session import DATABASE_URL
from infra.logging_config import configure_logging
from infra.queue.settings import QueueSettings

logger = configure_logging("worker", log_level=os.environ.get("LOG_LEVEL", "INFO"))

if os.name == "nt" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    # MCP's stdio transport uses its Popen fallback on the selector loop.
    # The default Windows proactor loop can fail creating its named pipe.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


async def startup(ctx: dict[str, Any]) -> None:
    # Per-worker-process resources go here as later milestones need them
    # (e.g. a shared object-storage client). Nothing needed yet.
    ctx["database_url"] = DATABASE_URL
    logger.info("Worker startup complete; queue=%s", QueueSettings().redis_url)


async def shutdown(ctx: dict[str, Any]) -> None:
    pass


class WorkerSettings:
    functions = [run_discovery]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = QueueSettings().arq_settings()
    job_timeout = int(os.environ.get("WORKER_JOB_TIMEOUT", "1200"))
    health_check_interval = 30
    # Required for Job.abort() to interrupt an in-progress crawl. The worker
    # remains alive and can immediately process the next queued job.
    allow_abort_jobs = True
    max_jobs = int(
        os.environ.get("WORKER_MAX_JOBS", "5")
    )  # bounds concurrent Discovery crawls — separate from
    # LLM_MAX_CONCURRENT_REQUESTS (infra/llm/rate_limiter.py), which bounds
    # concurrent LLM calls specifically; this bounds concurrent browser
    # sessions, a different, heavier resource.
