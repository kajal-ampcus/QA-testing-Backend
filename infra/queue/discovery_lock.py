"""One active Discovery job per project, tracked in Redis.

Two crawls writing the same project's canonical map interleave state codes
and checkpoints, and a project deleted mid-crawl leaves the worker writing
orphaned rows. The API claims this key before enqueueing; the worker releases
it when its job ends. The TTL outlives the worker's job timeout so a crashed
worker cannot hold the claim forever.
"""

import os
import uuid

from arq.connections import ArqRedis
from arq.jobs import Job, JobStatus

_ACTIVE_STATUSES = {JobStatus.queued, JobStatus.deferred, JobStatus.in_progress}


def _key(project_id: uuid.UUID | str) -> str:
    return f"qa:discovery:active:{project_id}"


def _ttl_seconds() -> int:
    try:
        timeout = int(os.environ.get("WORKER_JOB_TIMEOUT", "1200"))
    except ValueError:
        timeout = 1200
    return timeout + 300


async def active_discovery_job(pool: ArqRedis, project_id: uuid.UUID | str) -> str | None:
    """Return the id of a queued/running discovery job for the project, if any."""
    raw = await pool.get(_key(project_id))
    if raw is None:
        return None
    job_id = raw.decode() if isinstance(raw, bytes) else str(raw)
    if await Job(job_id, pool).status() in _ACTIVE_STATUSES:
        return job_id
    return None


async def claim_discovery(pool: ArqRedis, project_id: uuid.UUID | str) -> str | None:
    """Reserve a job id for a new discovery run, or return None if one is active."""
    job_id = uuid.uuid4().hex
    key = _key(project_id)
    if await pool.set(key, job_id, nx=True, ex=_ttl_seconds()):
        return job_id
    if await active_discovery_job(pool, project_id):
        return None
    # The recorded job has finished; replace the stale claim.
    await pool.set(key, job_id, ex=_ttl_seconds())
    return job_id


async def release_discovery(pool: ArqRedis, project_id: uuid.UUID | str, job_id: str) -> None:
    """Release the claim only if it still belongs to this job."""
    key = _key(project_id)
    raw = await pool.get(key)
    current = raw.decode() if isinstance(raw, bytes) else raw
    if current == job_id:
        await pool.delete(key)
