"""Check the same Redis connection the API and worker use before local startup.

Run from the backend directory: python -m scripts.check_redis
"""

import asyncio

from arq import create_pool
from pydantic import ValidationError
from redis.exceptions import RedisError

from infra.queue.settings import QueueSettings


async def check() -> int:
    try:
        settings = QueueSettings().arq_settings()
    except (ValueError, ValidationError):
        print("Redis configuration is invalid. Check REDIS_URL and REDIS_CONNECT_TIMEOUT in .env.")
        return 1
    settings.conn_retries = 0
    try:
        pool = await create_pool(settings)
        await pool.aclose()
    except (RedisError, OSError, TimeoutError) as exc:
        print(f"Redis unavailable at {settings.host}:{settings.port} ({type(exc).__name__}).")
        print("Start Redis/Memurai and check REDIS_URL in qa-platform/.env.")
        print("On Windows, use 127.0.0.1 instead of localhost for a local Redis service.")
        return 1
    print(f"Redis ready at {settings.host}:{settings.port}, database {settings.database}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
