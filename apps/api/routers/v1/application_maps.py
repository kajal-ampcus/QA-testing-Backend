"""
Application Map endpoints — surfaces core/agents/application_discovery output
(Section 9). Phase 0 stub.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/application-maps", tags=["application-maps"])

# TODO (Phase 1): trigger Discovery Agent (enqueues apps/worker/tasks/run_discovery.py),
# GET current map, GET map version history/diff
