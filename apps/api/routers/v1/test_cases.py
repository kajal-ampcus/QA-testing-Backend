"""
Test case endpoints — Test Design + Test Case Validation output (Sections 12, 6
Agent 3/4). Includes tester edit capability referenced in Section 27/23.
Phase 0 stub.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/test-cases", tags=["test-cases"])

# TODO (Phase 1): list/get test cases, PATCH for tester edits (versioned per Section 23),
# POST /test-cases/approve (human-approval gate, Section 21)
