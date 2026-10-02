"""
Worker task wrapping core/agents/test_execution — runs the compiled Playwright
suite (Section 14). Zero LLM-in-the-loop; this task's job is to invoke the
agent and persist test_runs/test_results/evidence, not to make any judgment
calls itself.
"""

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from domain.enums import TestRunStatus
from infra.db.models import (
    application_map,  # noqa: F401 — FK target of automation_scripts / test_cases
    automation,  # noqa: F401
    execution,  # noqa: F401
    project,  # noqa: F401
    requirement,  # noqa: F401
    test_case,  # noqa: F401 — automation_scripts.test_case_id
)
from infra.db.models.agent_run import AgentRun
from infra.db.repositories.automation_repo import AutomationRepository
from infra.db.repositories.execution_repo import ExecutionRepository
from infra.db.session import AsyncSessionLocal
from infra.object_storage.s3_client import S3Client
from schemas.envelope import AgentInputEnvelope, AgentOutputEnvelope, AgentRunStatus


def _job_timeout_seconds() -> int:
    try:
        return int(os.environ.get("WORKER_JOB_TIMEOUT", "1200"))
    except ValueError:
        return 1200


async def run_execution(ctx: dict[str, Any], project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """arq task: run the Test Execution Agent and persist results."""
    from core.agents.test_execution.agent import TestExecutionAgent

    async with AsyncSessionLocal() as session:
        execution_repo = ExecutionRepository(session)
        automation_repo = AutomationRepository(session)
        agent = TestExecutionAgent(
            execution_repo=execution_repo,
            automation_repo=automation_repo,
            s3=S3Client(),
        )
        timeout = max(30, _job_timeout_seconds() - 30)
        payload = {**payload, "timeout": int(payload.get("timeout") or timeout)}
        input_envelope = AgentInputEnvelope(
            agent_run_id=uuid.uuid4(),
            project_id=uuid.UUID(project_id),
            trigger="manual",
            payload=payload,
        )
        try:
            result = await agent.run(input_envelope)
        except Exception as exc:
            await session.rollback()
            run_id = payload.get("run_id")
            if run_id:
                stored = await ExecutionRepository(session).get_run(uuid.UUID(str(run_id)))
                if stored is not None:
                    ExecutionRepository(session).mark_finished(
                        stored,
                        TestRunStatus.FAILED,
                        {
                            **dict(stored.summary or {}),
                            "passed": int((stored.summary or {}).get("passed") or 0),
                            "failed": int((stored.summary or {}).get("failed") or 0),
                            "skipped": int((stored.summary or {}).get("skipped") or 0),
                            "error": max(int((stored.summary or {}).get("error") or 0), 1),
                            "detail": f"{type(exc).__name__}: {exc}"[:2000],
                        },
                    )
            session.add(
                AgentRun(
                    id=input_envelope.agent_run_id,
                    project_id=input_envelope.project_id,
                    agent_name=agent.name,
                    status=AgentRunStatus.FAILED,
                    input_envelope=input_envelope.model_dump(mode="json"),
                    output_envelope=AgentOutputEnvelope(
                        agent_run_id=input_envelope.agent_run_id,
                        status=AgentRunStatus.FAILED,
                        errors=[f"{type(exc).__name__}: {exc}"[:2000]],
                        token_usage=None,
                    ).model_dump(mode="json"),
                    finished_at=datetime.now(UTC),
                )
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
