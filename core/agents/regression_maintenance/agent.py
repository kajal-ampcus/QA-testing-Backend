"""
Agent 11 — Regression & Maintenance Agent (architecture doc Section 6, 25).
Thin scheduler, not a pipeline stage. Diffs application_map versions /
automation_versions (diffing.py) to decide whether a change is UI_DRIFT
(re-entry at AUTOMATION_GENERATED, likely selector healing) or a
FUNCTIONAL_CHANGE (re-entry at APPLICATION_DISCOVERED). Output
`re_entry_state` uses the literal Orchestrator state enum values, never free
text (companion doc Part 2 #11) — no interpretation step where a typo could
route into a nonexistent state.

Auto-triggering is deferred past MVP (Section 34) — starts with manual
re-run; webhook/schedule auto-triggering is Production scope (Section 35).

Phase 0 stub.
"""

# TODO (Phase 1/2): class RegressionMaintenanceAgent(BaseAgent): ...
