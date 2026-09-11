"""
The ONLY module that calls the Claude API directly (docs/PROJECT_STRUCTURE.md
point 8). Wraps the Anthropic client and enforces structured tool-use output
matching schemas/envelope.py's AgentOutputEnvelope shape, so a malformed
decision (missing evidence/source) fails here rather than propagating.

CRITICAL: core/agents/test_execution/ must never import this module — that
boundary is enforced via the `[tool.importlinter]` contract in pyproject.toml
and checked in CI (architecture doc Section 14's zero-LLM-in-the-loop rule).

Phase 0 stub.
"""

# TODO (Phase 1): class AnthropicClient: async def call(self, prompt, tools, ...) -> AgentOutputEnvelope: ...
