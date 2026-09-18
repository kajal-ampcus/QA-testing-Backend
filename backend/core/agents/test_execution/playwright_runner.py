"""
Thin wrapper around core/tool_gateway/playwright_client.py for running a
compiled test script and capturing the five evidence channels concurrently.
Deterministic infrastructure — no LLM calls, no judgment calls. This is the
one place in the whole system where a test's pass/fail status is actually
decided (by assertion result), matching architecture doc Section 14.

Phase 0 stub.
"""

# TODO (Phase 1): async def run_script(script_path, environment, test_data) -> TestResult: ...
