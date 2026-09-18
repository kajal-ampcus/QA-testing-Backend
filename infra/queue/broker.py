"""
Task broker — thin wrapper around arq's Redis pool for enqueueing jobs from
the API process. apps/worker/arq_worker.py is the separate process that
actually runs them (docs/PROJECT_STRUCTURE.md point 2) — the API only ever
enqueues and reads status, never runs a crawl inline.
"""

import os

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        settings.conn_retries = 1
        _pool = await create_pool(settings)
    return _pool


async def enqueue(task_name: str, *args: object) -> str:
    pool = await get_arq_pool()
    job = await pool.enqueue_job(task_name, *args)
    if job is None:
        raise RuntimeError(
            f"Failed to enqueue '{task_name}' — arq returned no job (possible dedup collision)"
        )
    return job.job_id
