"""
Defect endpoints — proposed vs. filed defects (Section 17), including the
mandatory human-approval-before-filing gate (Section 21). Phase 0 stub.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/defects", tags=["defects"])

# TODO (Phase 1): GET proposed defects, POST /defects/{id}/approve-and-file
# (calls Jira MCP via core/tool_gateway — deferred past MVP, see Section 34)
