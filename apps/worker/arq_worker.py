"""
arq worker entrypoint. Same core/ as apps/api — this process actually runs
Discovery crawls and (later, Milestone 6) Execution runs, which can take
minutes and don't belong inside an HTTP request/response cycle
(docs/PROJECT_STRUCTURE.md point 2).

Run with: arq apps.worker.arq_worker.WorkerSettings
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from arq import cron

from apps.worker.tasks.run_discovery import run_discovery
from apps.worker.tasks.run_execution import run_execution
from infra.db.session import DATABASE_URL
from infra.logging_config import configure_logging
from infra.queue.settings import QueueSettings

logger = configure_logging("worker", log_level=os.environ.get("LOG_LEVEL", "INFO"))

if os.name == "nt" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    # MCP's stdio transport uses its Popen fallback on the selector loop.
    # The default Windows proactor loop can fail creating its named pipe.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


def prune_discovery_evidence(protected: set[str] | None = None) -> int:
    """Delete unreferenced discovery screenshots older than the retention window.

    Files still cited by a map, diagnostic, input request, or execution result
    are kept. A missing protection set means the caller could not check
    references, so nothing is deleted.
    """
    if protected is None:
        return 0
    try:
        days = float(os.environ.get("DISCOVERY_EVIDENCE_RETENTION_DAYS", "30"))
    except ValueError:
        days = 30.0
    if days <= 0:
        return 0
    directory = Path(os.environ.get("DISCOVERY_EVIDENCE_DIR", "artifacts/discovery"))
    if not directory.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for screenshot in directory.glob("*.png"):
        try:
            if screenshot.name in protected:
                continue
            if screenshot.stat().st_mtime < cutoff:
                screenshot.unlink()
                removed += 1
        except OSError:
            continue
    return removed


async def referenced_discovery_files() -> set[str] | None:
    from sqlalchemy import select

    from core.agents.application_discovery.evidence import collect_evidence_names
    from infra.db.models.application_map import ApplicationMap, ApplicationMapState
    from infra.db.models.execution import TestResult
    from infra.db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            names: set[str] = set()
            for ref in await session.scalars(select(ApplicationMapState.evidence_ref)):
                names |= collect_evidence_names(ref)
            for payload in await session.scalars(select(ApplicationMap.diagnostic_evidence)):
                names |= collect_evidence_names(payload)
            for payload in await session.scalars(select(ApplicationMap.discovery_checkpoint)):
                names |= collect_evidence_names(payload)
            for payload in await session.scalars(select(TestResult.evidence)):
                names |= collect_evidence_names(payload)
        return names
    except Exception:
        logger.exception("Skipped discovery screenshot pruning because references could not be loaded")
        return None


async def prune_evidence_job(ctx: dict[str, Any]) -> None:
    protected = await referenced_discovery_files()
    removed = await asyncio.to_thread(prune_discovery_evidence, protected)
    if removed:
        logger.info("Removed %d expired discovery screenshot(s)", removed)


async def startup(ctx: dict[str, Any]) -> None:
    ctx["database_url"] = DATABASE_URL
    await prune_evidence_job(ctx)
    logger.info("Worker startup complete; queue=%s", QueueSettings().redis_url)


async def shutdown(ctx: dict[str, Any]) -> None:
    pass


class WorkerSettings:
    functions = [run_discovery, run_execution]
    cron_jobs = [cron(prune_evidence_job, hour={3}, minute={0}, run_at_startup=False)]
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
