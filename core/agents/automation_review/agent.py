"""
Agent 6 — Automation Review.

Independent static review of a generated suite. It does not launch a browser
and does not execute the tests. Node checks are reported as NOT_VERIFIED
when they were not run.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.agents.automation_generation.suite import update_manifest_verification
from core.agents.automation_review.lint_rules import LintResult, lint_suite
from core.agents.automation_review.node_verify import VerificationResult, not_verified, verify_suite
from core.agents.base import BaseAgent
from schemas.envelope import AgentInputEnvelope, AgentOutputEnvelope, AgentRunStatus


class ReviewOutcome:
    def __init__(self, lint: LintResult, verification: VerificationResult) -> None:
        self.lint = lint
        self.verification = verification


class AutomationReviewAgent(BaseAgent[ReviewOutcome]):
    name = "automation_review"

    async def run(self, request: AgentInputEnvelope) -> ReviewOutcome:
        suite_dir = Path(request.constraints["suite_dir"])
        forbidden = [str(item) for item in request.payload.get("forbidden_literals") or []]
        lint = lint_suite(suite_dir, forbidden_literals=forbidden)
        if request.constraints.get("run_node", True):
            verification = verify_suite(suite_dir)
        else:
            verification = not_verified("Node verification was not requested for this run.")
        report = {
            "executed": False,
            "lint": lint.as_dict(),
            "verification": verification.as_dict(),
        }
        (suite_dir / "review-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        update_manifest_verification(suite_dir, verification.status)
        return ReviewOutcome(lint, verification)

    @staticmethod
    def envelope(request: AgentInputEnvelope, outcome: ReviewOutcome) -> AgentOutputEnvelope:
        failed = outcome.verification.status == "FAILED"
        return AgentOutputEnvelope(
            agent_run_id=request.agent_run_id,
            status=AgentRunStatus.FAILED if failed else AgentRunStatus.SUCCESS,
            errors=[outcome.verification.detail] if failed and outcome.verification.detail else [],
        )
