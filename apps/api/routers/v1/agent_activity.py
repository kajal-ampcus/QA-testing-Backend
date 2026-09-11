"""
Feeds the "Agent Activity" transparency screen (Section 27) — a read-only feed
of agent_runs with their decisions/evidence/confidence, so the tester can see
what the AI is doing and why. Pairs with apps/api/websockets/agent_events.py
for the live version of this same data.

Phase 0 stub.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/agent-activity", tags=["agent-activity"])

# TODO (Phase 1): GET agent_runs feed, filterable by project/agent/status
