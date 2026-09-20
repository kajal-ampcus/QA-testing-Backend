"""
FastAPI app factory. Thin HTTP layer only — no business logic here.

core/ (agents, orchestrator, policy_safety, tool_gateway) is imported and
called from routers, never re-implemented inline. See docs/PROJECT_STRUCTURE.md
point 1: this module is allowed to import from core/, but nothing in core/
is allowed to import from here.

Milestone 1 — projects, requirements, and approvals are real.
Milestone 2 — application_maps is real (Discovery, enqueued via arq).
Test design generation and retrieval are also mounted.
Everything else in apps/api/routers/v1/ is still a Phase 0 stub and stays
unmounted until its own milestone gives it real content.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.routers.health import router as health_router
from apps.api.routers.v1.application_maps import router as application_maps_router
from apps.api.routers.v1.approvals import router as approvals_router
from apps.api.routers.v1.credentials import router as credentials_router
from apps.api.routers.v1.projects import router as projects_router
from apps.api.routers.v1.requirements import router as requirements_router
from apps.api.routers.v1.test_cases import router as test_cases_router
from apps.api.settings import ApiSettings


def create_app() -> FastAPI:
    app = FastAPI(title="QA Platform API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ApiSettings().cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Accept"],
    )

    app.include_router(health_router)
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(requirements_router, prefix="/api/v1")
    app.include_router(approvals_router, prefix="/api/v1")
    app.include_router(application_maps_router, prefix="/api/v1")
    app.include_router(credentials_router, prefix="/api/v1")
    app.include_router(test_cases_router, prefix="/api/v1")

    # TODO (later milestones): mount automation, executions,
    # failures, defects, reports, agent_activity as each gains real content
    # — see docs/IMPLEMENTATION_PLAN.md.
    # TODO (later milestones): register apps/api/middleware/* (tenant_scope,
    # request_logging, error_handling) — not needed for a single-tenant
    # smoke test, but shouldn't be forgotten once multi-project usage is real.
    # TODO (later milestones): mount apps/api/websockets/agent_events.py

    return app


app = create_app()
