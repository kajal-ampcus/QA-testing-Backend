"""
Live orchestrator state-transition stream (Section 27/28: "Real-Time Execution" —
renders things like "Agent: Application Discovery, Status: Running,
✓ Login discovered, → Discovering Edit Employee flow..."). Streams
core/orchestrator state-machine transition events as they happen; the UI
renders off these events directly, no polling.

Phase 0 stub.
"""

# TODO (Phase 1): WebSocket endpoint subscribing to orchestrator transition events
