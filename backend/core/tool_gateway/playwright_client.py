"""
Wraps the Playwright execution engine for the Test Execution Agent
(architecture doc Section 10/14) — the ONLY tool_gateway client used for
scripted test runs. Deliberately separate from mcp_clients/ (which are for
exploratory, agent-driven browser use) since Execution has zero LLM-in-the-
loop and should never share a code path with the exploratory tools.

Phase 0 stub.
"""

# TODO (Phase 1): class PlaywrightClient: async def run(self, script_path, ...) -> RawResult: ...
