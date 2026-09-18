"""
Agent 7 — Test Execution Agent, absorbs Browser Evidence/Observability
(architecture doc Section 6, 14). Runs reviewed automation via Playwright and
captures evidence (screenshot, video, trace, console_log, network_log) as a
side effect of execution. Pass/fail is a plain comparison of two
machine-readable values (assertion.expected vs. actual) — never an LLM
judgment call.

CRITICAL: this module must never import anything from infra/llm. That
boundary is enforced by the `[tool.importlinter]` contract in pyproject.toml
("test_execution agent has zero LLM-in-the-loop") and checked in CI
(.github/workflows/ci.yml) — this is a build-breaking rule, not a comment.

Phase 0 stub.
"""

# TODO (Phase 1): class TestExecutionAgent(BaseAgent): ...
#   delegates all browser work to playwright_runner.py — no reasoning here
