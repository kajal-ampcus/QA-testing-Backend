"""
Agent 3 — Test Design Agent, absorbs Test Strategy (architecture doc Section
6, 12). Risk-based test scenario/case generation from requirements +
application_map. Steps reference application-map state/element IDs, never
selectors (that's Agent 5's job — see companion doc Part 2 #3). Categories
with no applicable trigger are marked not_applicable, never silently skipped.

No browser access — reads the already-captured Application Map only. Calls
Test Data Agent (core/agents/test_data) as a service for field constraints.

Phase 0 stub.
"""

# TODO (Phase 1): class TestDesignAgent(BaseAgent): ...
