"""
Human-approval-gate endpoints — the structural implementation of Section 21.
Every gate in the pipeline (requirement interpretation, validated test cases,
destructive/production automation, defect filing) posts here. This is
deliberately its own router, not folded into each domain router, so every
approval action goes through one consistent, auditable path (writes to the
`approvals` table, Section 26).

Phase 0 stub.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/approvals", tags=["approvals"])

# TODO (Phase 1): GET pending approvals, POST /approvals/{id}/approve|reject
