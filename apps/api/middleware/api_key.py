"""Optional shared API key. Empty API_KEY leaves the API open for local development."""

from __future__ import annotations

import hmac
import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_PUBLIC_PATHS = frozenset({"/health", "/health/db", "/docs", "/openapi.json", "/redoc"})


class ApiKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str = "") -> None:
        super().__init__(app)
        self.api_key = api_key.strip()

    async def dispatch(self, request: Request, call_next):
        if not self.api_key or request.method == "OPTIONS" or request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        provided = request.headers.get("x-api-key", "")
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            provided = provided or authorization[7:].strip()
        if not provided or not hmac.compare_digest(provided, self.api_key):
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
        return await call_next(request)


def configured_api_key() -> str:
    return os.environ.get("API_KEY", "").strip()
