"""
Requirement endpoints — surfaces core/agents/requirement_understanding output
and the human-approval step (architecture doc Section 21). Phase 0 stub.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/requirements", tags=["requirements"])

# TODO (Phase 1): list/get requirements, trigger Requirement Understanding Agent,
# POST /requirements/{id}/approve
