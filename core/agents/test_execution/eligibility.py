"""Which reviewed automation scripts may enter a live Playwright run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from domain.enums import AutomationReviewStatus, RiskLevel

_BLOCKED = {
    AutomationReviewStatus.PENDING_APPROVAL,
    AutomationReviewStatus.REJECTED,
    AutomationReviewStatus.BLOCKED,
}
_RUNNABLE = {
    AutomationReviewStatus.REVIEWED,
    AutomationReviewStatus.APPROVED,
}


class ExecutionEligibilityError(Exception):
    """The request cannot start a run. The API maps this to HTTP 409."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class ScriptSnapshot:
    id: UUID
    file_path: str
    risk_level: str
    review_status: str
    expected_result: str = ""
    test_case_code: str = ""


@dataclass
class ClassifiedScripts:
    runnable: list[ScriptSnapshot]
    skipped: list[tuple[ScriptSnapshot, str]]


def resolve_pending_script_ids(
    requested: list[UUID] | None,
    pending: list[Any] | None,
) -> list[UUID] | None:
    """Use the caller's selection, or the specs written by the latest generate."""
    if requested is not None:
        return requested
    if not pending:
        return None
    return [UUID(str(item)) for item in pending]


def classify_scripts(
    scripts: list[ScriptSnapshot],
    requested_ids: list[UUID] | None,
    run_destructive: bool,
) -> ClassifiedScripts:
    """Split scripts into live-runnable vs skipped, or raise if none can run."""
    by_id = {script.id: script for script in scripts}
    if requested_ids is not None:
        if not requested_ids:
            raise ExecutionEligibilityError(
                "No automation scripts were selected. Select at least one reviewed script."
            )
        chosen: list[ScriptSnapshot] = []
        for script_id in requested_ids:
            script = by_id.get(script_id)
            if script is None:
                raise ExecutionEligibilityError(
                    f"Automation script {script_id} is not part of this generation."
                )
            chosen.append(script)
        selected = chosen
    else:
        selected = list(scripts)

    runnable: list[ScriptSnapshot] = []
    skipped: list[tuple[ScriptSnapshot, str]] = []
    for script in selected:
        reason = skip_reason(script, run_destructive)
        if reason:
            skipped.append((script, reason))
        else:
            runnable.append(script)

    if not runnable:
        raise ExecutionEligibilityError(
            "No reviewed automation scripts are eligible to run. "
            "Approve pending scripts, or enable destructive execution for approved destructive flows."
        )
    return ClassifiedScripts(runnable=runnable, skipped=skipped)


def skip_reason(script: ScriptSnapshot, run_destructive: bool) -> str | None:
    status = script.review_status
    risk = script.risk_level
    if status in _BLOCKED:
        return f"Script is {status.replace('_', ' ').lower()} and cannot run."
    if status not in _RUNNABLE:
        return f"Script review status {status} cannot be executed."
    if risk == RiskLevel.DESTRUCTIVE:
        if not run_destructive:
            return "Destructive flow is skipped unless RUN_DESTRUCTIVE is true."
        if status != AutomationReviewStatus.APPROVED:
            return "Destructive flow requires tester approval before it can run."
    return None
