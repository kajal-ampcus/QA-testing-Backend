"""Playwright execution report.

The counts and rows come from one finished test run. Discovery and test
generation are not part of this document.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.enums import EvidenceChannel, TestResultStatus, TestRunStatus
from infra.db.models.automation import AutomationScript
from infra.db.models.execution import TestResult, TestRun
from infra.db.models.requirement import Requirement
from infra.db.models.test_case import TestCaseVersion
from schemas.execution import (
    ExecutionReportCounts,
    ExecutionReportOut,
    ExecutionReportResult,
)

_FINISHED = {
    TestRunStatus.COMPLETED,
    TestRunStatus.FAILED,
    TestRunStatus.CANCELLED,
}
_FAILURES = {TestResultStatus.FAILED, TestResultStatus.ERROR}
_COUNT_KEYS = ("passed", "failed", "skipped", "error")


def run_is_finished(status: str) -> bool:
    return status in _FINISHED


def assemble_execution_report(
    run: TestRun,
    scripts: list[AutomationScript],
    versions: list[TestCaseVersion],
    requirements: list[Requirement],
) -> ExecutionReportOut:
    script_by_id = {script.id: script for script in scripts}
    version_by_key = {(item.test_case_id, item.version): item for item in versions}
    requirement_by_id = {item.id: item for item in requirements}
    rows = [
        _result_row(result, script_by_id, version_by_key, requirement_by_id)
        for result in run.results or []
    ]
    rows.sort(key=lambda row: (0 if row.status in _FAILURES else 1, row.test_case_code))
    return ExecutionReportOut(
        run_id=run.id,
        project_id=run.project_id,
        generation_id=run.generation_id,
        environment=run.environment,
        base_url=run.base_url,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        counts=_counts(run, rows),
        results=rows,
    )


async def load_execution_report(
    session: AsyncSession, run: TestRun
) -> ExecutionReportOut:
    script_ids = [
        result.automation_script_id
        for result in run.results or []
        if result.automation_script_id is not None
    ]
    scripts = await _scripts(session, script_ids)
    case_ids = list({script.test_case_id for script in scripts})
    versions = await _versions(session, case_ids)
    requirement_ids = list(
        {script.requirement_id for script in scripts if script.requirement_id is not None}
    )
    requirements = await _requirements(session, requirement_ids)
    return assemble_execution_report(run, scripts, versions, requirements)


def _counts(run: TestRun, rows: list[ExecutionReportResult]) -> ExecutionReportCounts:
    raw = dict(run.summary or {})
    counts = {key: _as_int(raw.get(key)) for key in _COUNT_KEYS}
    if sum(counts.values()) == 0 and rows:
        for row in rows:
            key = row.status.lower()
            if key in counts:
                counts[key] += 1
            else:
                counts["error"] += 1
    total = sum(counts.values())
    duration = sum(row.duration_ms or 0 for row in rows)
    rate = round((counts["passed"] / total) * 100, 1) if total else 0.0
    return ExecutionReportCounts(
        total=total,
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        error=counts["error"],
        pass_rate=rate,
        duration_ms=duration,
    )


def _result_row(
    result: TestResult,
    script_by_id: dict[uuid.UUID, AutomationScript],
    version_by_key: dict[tuple[uuid.UUID, int], TestCaseVersion],
    requirement_by_id: dict[uuid.UUID, Requirement],
) -> ExecutionReportResult:
    script = (
        script_by_id.get(result.automation_script_id)
        if result.automation_script_id is not None
        else None
    )
    version = None
    requirement_code = ""
    code = ""
    if script is not None:
        code = script.test_case_code
        version = version_by_key.get((script.test_case_id, script.test_case_version))
        if script.requirement_id is not None:
            requirement = requirement_by_id.get(script.requirement_id)
            requirement_code = requirement.req_code if requirement is not None else ""
    assertion = result.assertion or {}
    written = (version.expected_result if version is not None else "") or ""
    expected = written.strip() or str(assertion.get("expected") or "")
    return ExecutionReportResult(
        id=result.id,
        test_case_code=code or result.spec_path,
        title=(version.title if version is not None else "") or code or result.spec_path,
        requirement_code=requirement_code,
        status=result.status,
        expected=expected,
        actual=str(assertion.get("actual") or ""),
        duration_ms=result.duration_ms,
        error_message=result.error_message,
        evidence=_evidence(result.evidence or {}),
    )


def _evidence(evidence: dict[str, object]) -> list[str]:
    present: list[str] = []
    for channel in EvidenceChannel:
        value = evidence.get(channel.value)
        if isinstance(value, str) and value.strip():
            present.append(channel.value)
    return present


def _as_int(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


async def _scripts(session: AsyncSession, script_ids: list[uuid.UUID]) -> list[AutomationScript]:
    if not script_ids:
        return []
    result = await session.execute(
        select(AutomationScript).where(AutomationScript.id.in_(script_ids))
    )
    return list(result.scalars().all())


async def _versions(session: AsyncSession, case_ids: list[uuid.UUID]) -> list[TestCaseVersion]:
    if not case_ids:
        return []
    result = await session.execute(
        select(TestCaseVersion).where(TestCaseVersion.test_case_id.in_(case_ids))
    )
    return list(result.scalars().all())


async def _requirements(session: AsyncSession, requirement_ids: list[uuid.UUID]) -> list[Requirement]:
    if not requirement_ids:
        return []
    result = await session.execute(
        select(Requirement).where(Requirement.id.in_(requirement_ids))
    )
    return list(result.scalars().all())
