"""HTTP request logging middleware for the API service."""

from __future__ import annotations

import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from infra.logging_config import configure_logging

logger = configure_logging("api")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "HTTP %s %s failed in %.2f ms",
                request.method,
                request.url.path,
                elapsed_ms,
            )
            raise

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "HTTP %s %s -> %s in %.2f ms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response
