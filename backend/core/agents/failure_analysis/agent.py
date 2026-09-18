"""
Agent 8 — Failure Analysis & Defect Agent, absorbs Failure Reproduction +
Defect Analysis (architecture doc Section 6, 16, 17). Given a failed run's
evidence bundle: runs deterministic checks first (HTTP status, accessibility-
tree match for selector drift), reproduces via chrome-devtools-mcp
(reproduction.py) to determine DETERMINISTIC vs. FLAKY, classifies into the
11-value taxonomy (classification_taxonomy.py), and proposes a defect if
warranted. Every classification must cite specific evidence — never
"the LLM felt it was a bug" (companion doc Part 2 #8).

If evidence is insufficient, outputs UNKNOWN and escalates — never forces a
guess into a named category. Human approval required before a defect is
actually filed externally.

Phase 0 stub.
"""

# TODO (Phase 1): class FailureAnalysisAgent(BaseAgent): ...
