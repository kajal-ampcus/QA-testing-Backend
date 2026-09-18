"""
Agent 10 — Test Data Agent (architecture doc Section 6, 20). Cross-cutting
SERVICE, not a pipeline stage — called by Test Design and Test Execution, not
invoked by the orchestrator directly. Generates valid/invalid/boundary/
dependent/cleanup data against discovered field constraints. Every request
carries a run_scope_id so created records are traceable to the exact run
that created them, enabling precise cleanup (companion doc Part 2 #10) —
never a "delete everything from today" heuristic.

Credentials/secrets are never generated or handled here — that's
core/tool_gateway/secret_resolver.py exclusively.

Phase 0 stub.
"""

# TODO (Phase 1): class TestDataAgent(BaseAgent): ...
