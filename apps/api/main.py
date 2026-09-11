"""
FastAPI app factory. Thin HTTP layer only — no business logic here.

core/ (agents, orchestrator, policy_safety, tool_gateway) is imported and
called from routers, never re-implemented inline. See docs/PROJECT_STRUCTURE.md
point 1: this module is allowed to import from core/, but nothing in core/
is allowed to import from here.

Phase 0 stub — router mounting happens as each router in apps/api/routers/
gets real content in Phase 1.
"""

from fastapi import FastAPI

from apps.api.routers.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(title="QA Platform API", version="0.1.0")

    app.include_router(health_router)

    # TODO (Phase 1): mount apps/api/routers/v1/* (needs real schemas/repositories first)
    # TODO (Phase 1): register apps/api/middleware/* (tenant_scope, request_logging, error_handling)
    # TODO (Phase 1): mount apps/api/websockets/agent_events.py

    return app


app = create_app()
