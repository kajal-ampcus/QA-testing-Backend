"""
Agent 1 — Requirement Understanding Agent (architecture doc Section 6).

Converts free-text requirements into structured, traceable, testable
requirement records with REQ-ID + version. LLM only — no browser, no MCP
(giving it zero tools is what guarantees it can't hallucinate a browsing
action; see companion doc Part 2 #1). Ambiguous/contradictory requirements
are flagged NEEDS_CLARIFICATION, never silently resolved.

Human approval required before Application Discovery starts (Section 21).

Phase 0 stub.
"""

# TODO (Phase 1): class RequirementUnderstandingAgent(BaseAgent): ...
