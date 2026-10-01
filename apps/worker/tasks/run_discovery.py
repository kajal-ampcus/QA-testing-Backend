"""
Worker task wrapping core/agents/application_discovery — the arq function the
API enqueues rather than running Discovery synchronously inside a request.
Owns its own DB session (arq tasks run in a separate process from the API,
so they can't reuse apps.api.dependencies.get_db_session, which is FastAPI-
specific dependency injection).
"""

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from infra.db.models import project  # noqa: F401 — register the FK target on Base.metadata
from infra.db.models.agent_run import AgentRun
from infra.db.repositories.application_map_repo import ApplicationMapRepository
from infra.db.repositories.requirement_repo import RequirementRepository
from infra.db.session import AsyncSessionLocal
from infra.queue.discovery_lock import release_discovery
from schemas.envelope import AgentInputEnvelope, AgentOutputEnvelope, AgentRunStatus


def _job_timeout_seconds() -> int:
    try:
        return int(os.environ.get("WORKER_JOB_TIMEOUT", "1200"))
    except ValueError:
        return 1200


def _diagnostic(payload: dict[str, Any], detail: str, failed_actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "auth_attempted": bool(payload.get("target", {}).get("credential_ref")),
        "auth_succeeded": False,
        "login_error": None,
        "screenshot_ref": None,
        "console_errors": [],
        "network_errors": [],
        "failed_actions": failed_actions,
        "termination_detail": detail,
    }


async def run_discovery(
    ctx: dict[str, Any], project_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """arq task signature: first arg is always the worker context (ctx),
    everything after is what the caller passed to `enqueue_job`. Returns a
    small JSON-safe summary — arq stores this as the job result, retrievable
    via the job id for the /application-maps status endpoint to poll."""
    # Import here, not at module load time, to keep the dependency direction
    # explicit and one-way: worker task -> agent, never the reverse.
    from core.agents.application_discovery.agent import ApplicationDiscoveryAgent

    started = time.monotonic()
    try:
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

            async def _close_out(
                termination_reason: str,
                coverage_extra: dict[str, Any],
                diagnostic: dict[str, Any],
                error: str,
                run_status: AgentRunStatus,
            ) -> None:
                # States and checkpoints were committed incrementally; keep
                # them and make this run's own map inspectable/resumable
                # instead of leaving it RUNNING forever.
                await session.rollback()
                if agent.application_map_id is not None:
                    app_map = await map_repo.get_with_states(agent.application_map_id)
                    if app_map is not None and app_map.status == "RUNNING":
                        await map_repo.set_status(
                            app_map.id,
                            "PARTIAL",
                            termination_reason=termination_reason,
                            coverage={
                                **(app_map.coverage or {}),
                                **coverage_extra,
                                "states_discovered": await map_repo.count_states(app_map.id),
                            },
                            diagnostic_evidence=diagnostic,
                        )
                session.add(
                    AgentRun(
                        id=input_envelope.agent_run_id,
                        project_id=input_envelope.project_id,
                        agent_name=agent.name,
                        status=run_status,
                        input_envelope=input_envelope.model_dump(mode="json"),
                        output_envelope=AgentOutputEnvelope(
                            agent_run_id=input_envelope.agent_run_id,
                            status=run_status,
                            errors=[error],
                        ).model_dump(mode="json"),
                        finished_at=datetime.now(UTC),
                    )
                )
                await session.commit()

            try:
                result = await agent.run(input_envelope)
            except asyncio.CancelledError:
                # arq enforces job_timeout by cancelling the task, exactly like
                # a user abort. Elapsed time tells the two apart.
                timed_out = time.monotonic() - started >= _job_timeout_seconds() - 2
                reason = "MAX_DURATION_REACHED" if timed_out else "CANCELLED_BY_USER"
                detail = (
                    "Discovery reached the worker time limit. Continue from the saved checkpoint."
                    if timed_out
                    else "Discovery was stopped by the user."
                )
                await asyncio.shield(
                    _close_out(
                        reason,
                        {"cancelled": not timed_out, "recoverable": True},
                        _diagnostic(payload, detail, []),
                        detail,
                        AgentRunStatus.PARTIAL,
                    )
                )
                raise
            except Exception as exc:
                await _close_out(
                    "WORKER_FAILURE",
                    {"recoverable": True},
                    _diagnostic(
                        payload,
                        "The discovery worker stopped unexpectedly. "
                        "Saved observations can be continued.",
                        [{"error": type(exc).__name__, "phase": "worker"}],
                    ),
                    f"{type(exc).__name__}: {exc}"[:2000],
                    AgentRunStatus.FAILED,
                )
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
    finally:
        redis = ctx.get("redis")
        job_id = ctx.get("job_id")
        if redis is not None and job_id:
            await asyncio.shield(release_discovery(redis, project_id, job_id))
