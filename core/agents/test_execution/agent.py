"""
Agent 7 — Test Execution Agent, absorbs Browser Evidence/Observability
(architecture doc Section 6, 14). Runs reviewed automation via Playwright and
captures evidence (screenshot, video, trace, console_log, network_log) as a
side effect of execution. Pass/fail is a plain comparison of two
machine-readable values (assertion.expected vs. actual) — never an LLM
judgment call.

CRITICAL: this module must never import anything from infra.llm. That
boundary is enforced by the `[tool.importlinter]` contract in pyproject.toml
("test_execution agent has zero LLM-in-the-loop") and checked in CI
(.github/workflows/ci.yml) — this is a build-breaking rule, not a comment.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from core.agents.base import BaseAgent
from core.agents.test_execution.eligibility import (
    ClassifiedScripts,
    ExecutionEligibilityError,
    ScriptSnapshot,
    classify_scripts,
)
from core.agents.test_execution.live_display import claim_live_display, release_live_display
from core.agents.test_execution.playwright_runner import PlaywrightRunner, RawTestOutcome
from domain.enums import (
    EvidenceChannel,
    EvidenceSource,
    TestResultStatus,
    TestRunStatus,
)
from infra.db.models.automation import AutomationScript
from infra.db.models.execution import TestResult, TestRun
from infra.db.repositories.automation_repo import AutomationRepository
from infra.db.repositories.execution_repo import ExecutionRepository
from infra.object_storage.s3_client import InMemoryS3Client, S3Client
from schemas.envelope import AgentInputEnvelope, AgentOutputEnvelope, AgentRunStatus


class TestExecutionAgent(BaseAgent[AgentOutputEnvelope]):
    __test__ = False
    name = "test_execution"

    def __init__(
        self,
        execution_repo: ExecutionRepository,
        automation_repo: AutomationRepository,
        s3: S3Client | InMemoryS3Client,
        runner: PlaywrightRunner | None = None,
    ) -> None:
        self.execution_repo = execution_repo
        self.automation_repo = automation_repo
        self.s3 = s3
        self.runner = runner or PlaywrightRunner()

    async def run(self, request: AgentInputEnvelope) -> AgentOutputEnvelope:
        payload = request.payload
        run_id = UUID(str(payload["run_id"]))
        generation_id = UUID(str(payload["generation_id"]))
        suite_dir = Path(str(payload["suite_dir"]))
        run_destructive = bool(payload.get("run_destructive"))
        requested = payload.get("script_ids")
        requested_ids = [UUID(str(item)) for item in requested] if requested else None

        run = await self.execution_repo.get_run(run_id)
        if run is None:
            return AgentOutputEnvelope(
                agent_run_id=request.agent_run_id,
                status=AgentRunStatus.FAILED,
                errors=["Test run row is missing."],
                token_usage=None,
            )

        scripts = await self.automation_repo.list_generation(request.project_id, generation_id)
        snapshots = [_snapshot(script, payload) for script in scripts]
        try:
            classified = classify_scripts(snapshots, requested_ids, run_destructive)
        except ExecutionEligibilityError as exc:
            self.execution_repo.mark_finished(
                run,
                TestRunStatus.FAILED,
                {"passed": 0, "failed": 0, "skipped": 0, "error": 1},
            )
            return AgentOutputEnvelope(
                agent_run_id=request.agent_run_id,
                status=AgentRunStatus.BLOCKED,
                errors=[exc.message],
                token_usage=None,
            )

        self.execution_repo.mark_running(run)
        await self.execution_repo.session.commit()
        await claim_live_display(str(request.project_id), str(run.id))
        try:
            try:
                live = await self._run_live(run, request, payload, suite_dir, classified)
            except Exception as exc:  # noqa: BLE001 - persist the failure, then re-raise for the worker
                await self._reload_status(run)
                if run.status != TestRunStatus.CANCELLED:
                    self.execution_repo.mark_finished(
                        run,
                        TestRunStatus.FAILED,
                        {
                            "passed": 0,
                            "failed": 0,
                            "skipped": 0,
                            "error": 1,
                            "detail": f"{type(exc).__name__}: {exc}"[:2000],
                        },
                    )
                    await self.execution_repo.session.flush()
                raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            await release_live_display(str(run.id))

        await self._reload_status(run)
        if run.status == TestRunStatus.CANCELLED:
            return AgentOutputEnvelope(
                agent_run_id=request.agent_run_id,
                status=AgentRunStatus.FAILED,
                errors=["Execution was stopped."],
                token_usage=None,
            )

        summary = _summary(list(run.results))
        log = _playwright_log(live)
        if log:
            summary["log"] = log
        if live.detail:
            summary["detail"] = live.detail[:2000]
        final_status = (
            TestRunStatus.FAILED
            if summary["failed"] or summary["error"]
            else TestRunStatus.COMPLETED
        )
        if live.process_ok is False and not live.outcomes:
            final_status = TestRunStatus.FAILED
            summary["error"] = max(summary["error"], 1)
        self.execution_repo.mark_finished(run, final_status, summary)
        _mark_generation_executed(scripts, suite_dir)
        await self.execution_repo.session.flush()

        envelope_status = (
            AgentRunStatus.SUCCESS
            if final_status == TestRunStatus.COMPLETED and summary["failed"] == 0
            else AgentRunStatus.PARTIAL
            if summary["passed"]
            else AgentRunStatus.FAILED
        )
        return AgentOutputEnvelope(
            agent_run_id=request.agent_run_id,
            status=envelope_status,
            artifacts=[{"type": "test_run", "id": str(run.id), "version": 1}],
            decisions=[
                {
                    "decision": f"{outcome.status}:{outcome.spec_path}",
                    "reason": outcome.error_message or f"Assertion compared {outcome.expected!r} with {outcome.actual!r}",
                    "evidence": [f"assertion:{outcome.expected}->{outcome.actual}"],
                    "confidence": 1.0,
                    "source": EvidenceSource.TEST_EXECUTION,
                }
                for outcome in live.outcomes[:50]
            ]
            or [
                {
                    "decision": final_status,
                    "reason": live.detail or "Suite finished without a JSON report.",
                    "evidence": [f"run:{run.id}"],
                    "confidence": 1.0,
                    "source": EvidenceSource.TEST_EXECUTION,
                }
            ],
            errors=[live.detail] if live.detail else [],
            token_usage=None,
        )

    async def _run_live(
        self,
        run: TestRun,
        request: AgentInputEnvelope,
        payload: dict[str, Any],
        suite_dir: Path,
        classified: ClassifiedScripts,
    ) -> Any:
        from core.agents.test_execution.playwright_runner import SuiteRunResult

        for script, reason in classified.skipped:
            self._store_result(
                run,
                TestResult(
                    id=uuid.uuid4(),
                    test_run_id=run.id,
                    automation_script_id=script.id,
                    spec_path=script.file_path,
                    status=TestResultStatus.SKIPPED,
                    assertion={
                        "expected": "skipped",
                        "actual": "skipped",
                        "source": EvidenceSource.TEST_EXECUTION,
                    },
                    evidence={channel.value: None for channel in EvidenceChannel},
                    error_message=reason,
                ),
            )

        live = await self.runner.run_suite(
            suite_dir,
            scripts=classified.runnable,
            project_id=request.project_id,
            base_url=payload.get("base_url"),
            credential_ref=payload.get("credential_ref"),
            run_destructive=bool(payload.get("run_destructive")),
            timeout=int(payload.get("timeout") or 900),
            runner=payload.get("subprocess_runner"),
        )
        if not isinstance(live, SuiteRunResult):
            return live
        for outcome in live.outcomes:
            result_id = uuid.uuid4()
            evidence = await self._upload(
                project_id=str(request.project_id),
                run_id=str(payload["run_id"]),
                result_id=str(result_id),
                outcome=outcome,
            )
            self._store_result(
                run,
                TestResult(
                    id=result_id,
                    test_run_id=run.id,
                    automation_script_id=outcome.script_id,
                    spec_path=outcome.spec_path,
                    status=outcome.status,
                    assertion={
                        "expected": outcome.expected,
                        "actual": outcome.actual,
                        "source": EvidenceSource.TEST_EXECUTION,
                    },
                    evidence=evidence,
                    duration_ms=outcome.duration_ms,
                    error_message=outcome.error_message,
                ),
            )
        if not live.outcomes and not live.process_ok:
            for script in classified.runnable:
                self._store_result(
                    run,
                    TestResult(
                        id=uuid.uuid4(),
                        test_run_id=run.id,
                        automation_script_id=script.id,
                        spec_path=script.file_path,
                        status=TestResultStatus.ERROR,
                        assertion={
                            "expected": "pass",
                            "actual": live.detail or "Playwright produced no JSON report.",
                            "source": EvidenceSource.TEST_EXECUTION,
                        },
                        evidence={channel.value: None for channel in EvidenceChannel},
                        error_message=live.detail or "Playwright produced no JSON report.",
                    ),
                )
        return live

    async def _reload_status(self, run: TestRun) -> None:
        """Re-read status only. Refreshing the whole row drops unsaved results."""
        await self.execution_repo.session.refresh(run, attribute_names=["status"])

    def _store_result(self, run: TestRun, result: TestResult) -> TestResult:
        run.results.append(result)
        return self.execution_repo.add_result(result)

    async def _upload(
        self,
        *,
        project_id: str,
        run_id: str,
        result_id: str,
        outcome: RawTestOutcome,
    ) -> dict[str, str | None]:
        refs: dict[str, str | None] = {channel.value: None for channel in EvidenceChannel}
        blobs: dict[str, bytes | None] = {
            EvidenceChannel.SCREENSHOT.value: outcome.screenshot,
            EvidenceChannel.VIDEO.value: outcome.video,
            EvidenceChannel.TRACE.value: outcome.trace,
            EvidenceChannel.CONSOLE_LOG.value: outcome.console_log,
            EvidenceChannel.NETWORK_LOG.value: outcome.network_log,
        }
        for channel, data in blobs.items():
            if data is None:
                continue
            try:
                refs[channel] = await self.s3.put_evidence(
                    project_id=project_id,
                    run_id=run_id,
                    result_id=result_id,
                    channel=channel,
                    data=data,
                )
            except Exception:  # noqa: BLE001 - a missing channel must not drop the result row
                refs[channel] = None
        return refs


def _snapshot(script: AutomationScript, payload: dict[str, Any]) -> ScriptSnapshot:
    expected = ""
    extras = payload.get("expected_results") or {}
    if isinstance(extras, dict):
        expected = str(extras.get(str(script.id)) or extras.get(script.test_case_code) or "")
    findings = script.review_findings or {}
    if not expected:
        expected = str(findings.get("expected_result") or "")
    return ScriptSnapshot(
        id=script.id,
        file_path=script.file_path,
        risk_level=script.risk_level,
        review_status=script.review_status,
        expected_result=expected,
        test_case_code=script.test_case_code,
    )


def _summary(results: list[TestResult]) -> dict[str, Any]:
    counts: dict[str, Any] = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    for result in results:
        key = result.status.lower()
        if key in counts:
            counts[key] += 1
        else:
            counts["error"] += 1
    return counts


def _playwright_log(live: Any) -> str:
    raw = getattr(live, "raw", None)
    if raw is None:
        return ""
    stdout = str(getattr(raw, "stdout", "") or "")
    stderr = str(getattr(raw, "stderr", "") or "")
    return f"{stdout}\n{stderr}".strip()[-8000:]


def _mark_generation_executed(scripts: list[AutomationScript], suite_dir: Path) -> None:
    for script in scripts:
        summary = dict(script.suite_summary or {})
        summary["executed"] = True
        script.suite_summary = summary
    manifest = suite_dir / "manifest.json"
    if not manifest.is_file():
        return
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    data["executed"] = True
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
