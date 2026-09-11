"""
Failure reproduction (architecture doc Section 16): clean chrome-devtools-mcp
session (--isolated), fresh test data via core/agents/test_data, 1-3 re-runs.
Consistent failure -> DETERMINISTIC; mixed pass/fail -> FLAKY_TEST, routed to
flaky statistics instead of a filed defect. This is exploratory (an agent
picking actions turn-by-turn) so it uses chrome-devtools-mcp, not a
re-invocation of the compiled Playwright script (Section 10).

Phase 0 stub.
"""

# TODO (Phase 1): async def reproduce(failure, evidence, max_attempts=3) -> ReproductionVerdict: ...
