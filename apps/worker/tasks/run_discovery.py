"""
Worker task wrapping core/agents/application_discovery — the arq function the
API enqueues rather than running Discovery synchronously inside a request.
Owns its own DB session (arq tasks run in a separate process from the API,
so they can't reuse apps.api.dependencies.get_db_session, which is FastAPI-
specific dependency injection).
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from infra.db.models import project  # noqa: F401 — register the FK target on Base.metadata
from infra.db.models.agent_run import AgentRun
from infra.db.repositories.application_map_repo import ApplicationMapRepository
from infra.db.repositories.requirement_repo import RequirementRepository
from infra.db.session import AsyncSessionLocal
from schemas.envelope import AgentInputEnvelope


async def run_discovery(
    ctx: dict[str, Any], project_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """arq task signature: first arg is always the worker context (ctx),
    everything after is what the caller passed to `enqueue_job`. Returns a
    small JSON-safe summary — arq stores this as the job result, retrievable
    via the job id for the /application-maps status endpoint to poll."""
    # Import here, not at module load time, to avoid a circular import
    # between this module and core.agents.application_discovery.agent (which
    # doesn't currently import this file, but keeps the dependency direction
    # explicit and one-way: worker task -> agent, never the reverse).
    from core.agents.application_discovery.agent import ApplicationDiscoveryAgent

    async with AsyncSessionLocal() as session:
        map_repo = ApplicationMapRepository(session)
        requirement_repo = RequirementRepository(session)
        agent = ApplicationDiscoveryAgent(map_repo=map_repo, requirement_repo=requirement_repo)

        input_envelope = AgentInputEnvelope(
            agent_run_id=uuid.uuid4(),
            project_id=uuid.UUID(project_id),
            trigger="manual",
            payload=payload,
        )
        try:
            result = await agent.run(input_envelope)
        except asyncio.CancelledError:
            # Discovery states are committed incrementally. Preserve them and
            # make the latest map usable/inspectable instead of leaving it in
            # RUNNING forever after the ARQ job is aborted.
            await session.rollback()
            app_map = await map_repo.get_latest_for_project(input_envelope.project_id)
            if app_map is not None and app_map.status == "RUNNING":
                await map_repo.set_status(
                    app_map.id,
                    "PARTIAL",
                    termination_reason="CANCELLED_BY_USER",
                    coverage={
                        **(app_map.coverage or {}),
                        "cancelled": True,
                        "states_discovered": await map_repo.count_states(app_map.id),
                    },
                    diagnostic_evidence={
                        "auth_attempted": bool(
                            payload.get("target", {}).get("credential_ref")
                        ),
                        "auth_succeeded": False,
                        "login_error": None,
                        "screenshot_ref": None,
                        "console_errors": [],
                        "network_errors": [],
                        "failed_actions": [],
                        "termination_detail": "Discovery was stopped by the user.",
                    },
                )
                await session.commit()
            raise
        except Exception as exc:
            # Preserve committed graph/checkpoint data and make unexpected
            # worker failures resumable instead of leaving the map RUNNING.
            await session.rollback()
            app_map = await map_repo.get_latest_for_project(input_envelope.project_id)
            if app_map is not None and app_map.status == "RUNNING":
                await map_repo.set_status(
                    app_map.id,
                    "PARTIAL",
                    termination_reason="WORKER_FAILURE",
                    coverage={
                        **(app_map.coverage or {}),
                        "states_discovered": await map_repo.count_states(app_map.id),
                        "recoverable": True,
                    },
                    diagnostic_evidence={
                        "auth_attempted": bool(
                            payload.get("target", {}).get("credential_ref")
                        ),
                        "auth_succeeded": False,
                        "login_error": None,
                        "screenshot_ref": None,
                        "console_errors": [],
                        "network_errors": [],
                        "termination_detail": (
                            "The discovery worker stopped unexpectedly. "
                            "Saved observations can be continued."
                        ),
                        "failed_actions": [
                            {"error": type(exc).__name__, "phase": "worker"}
                        ],
                    },
                )
                await session.commit()
            raise
        session.add(
            AgentRun(
                id=input_envelope.agent_run_id,
                project_id=input_envelope.project_id,
                agent_name=agent.name,
                status=result.status,
                input_envelope=input_envelope.model_dump(mode="json"),
                output_envelope=result.model_dump(mode="json"),
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        return result.model_dump(mode="json")
