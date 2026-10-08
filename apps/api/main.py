"""
FastAPI app factory. Thin HTTP layer only — no business logic here.

core/ (agents, orchestrator, policy_safety, tool_gateway) is imported and
called from routers, never re-implemented inline. See docs/PROJECT_STRUCTURE.md
point 1: this module is allowed to import from core/, but nothing in core/
is allowed to import from here.

Milestone 1 — projects, requirements, and approvals are real.
Milestone 2 — application_maps is real (Discovery, enqueued via arq).
Test design generation and retrieval are also mounted.
Automation generation/review and test execution are mounted.
Failures, defects, and remaining Phase 0 stubs stay unmounted.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.middleware.api_key import ApiKeyMiddleware
from apps.api.middleware.error_handling import register_error_handlers
from apps.api.middleware.request_logging import RequestLoggingMiddleware
from apps.api.routers.health import router as health_router
from apps.api.routers.v1.agent_activity import router as agent_activity_router
from apps.api.routers.v1.application_maps import router as application_maps_router
from apps.api.routers.v1.approvals import router as approvals_router
from apps.api.routers.v1.automation import router as automation_router
from apps.api.routers.v1.credentials import router as credentials_router
from apps.api.routers.v1.executions import router as executions_router
from apps.api.routers.v1.projects import router as projects_router
from apps.api.routers.v1.reports import router as reports_router
from apps.api.routers.v1.saved_inputs import router as saved_inputs_router
from apps.api.routers.v1.requirements import router as requirements_router
from apps.api.routers.v1.test_cases import router as test_cases_router
from apps.api.settings import ApiSettings
from infra.logging_config import configure_logging

api_logger = configure_logging("api", log_level=ApiSettings().log_level)


def create_app() -> FastAPI:
    api_logger.info("API startup complete; logger initialized at %s", ApiSettings().cors_origins)
    app = FastAPI(title="QA Platform API", version="0.1.0")
    app.state.logger = api_logger
    settings = ApiSettings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Accept", "Authorization", "X-API-Key"],
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(ApiKeyMiddleware, api_key=settings.api_key)
    register_error_handlers(app)

    app.include_router(health_router)
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(requirements_router, prefix="/api/v1")
    app.include_router(approvals_router, prefix="/api/v1")
    app.include_router(application_maps_router, prefix="/api/v1")
    app.include_router(credentials_router, prefix="/api/v1")
    app.include_router(saved_inputs_router, prefix="/api/v1")
    app.include_router(test_cases_router, prefix="/api/v1")
    app.include_router(automation_router, prefix="/api/v1")
    app.include_router(executions_router, prefix="/api/v1")
    app.include_router(reports_router, prefix="/api/v1")
    app.include_router(agent_activity_router, prefix="/api/v1")

    return app


app = create_app()
