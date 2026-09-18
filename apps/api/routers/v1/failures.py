"""
Failure endpoints — Failure Analysis & Defect Agent classification output
(Section 16, 6 Agent 8), including the evidence viewer and reproduction status
referenced in Section 27. Phase 0 stub.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/failures", tags=["failures"])

# TODO (Phase 1): GET failures + classification + evidence refs, GET reproduction status
