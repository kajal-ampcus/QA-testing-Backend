"""
The single choke point for every external tool call (architecture doc Section
11/28). No agent module imports mcp_clients/ or infra/secrets/ directly — it
goes through here (docs/PROJECT_STRUCTURE.md point 4). This is Section 11's
least-privilege table made real: each agent is provisioned with only the tool
subset it needs, enforced here, not by prompting.

Phase 0 stub.
"""

# TODO (Phase 1): class ToolGateway:
#   def for_agent(self, agent_name: str) -> ScopedToolAccess: ...
#   — returns only the tools that agent is allowed per Section 11's table
