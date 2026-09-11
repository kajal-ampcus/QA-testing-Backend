"""
Agent 6 — Automation Review Agent (architecture doc Section 6). Independent
code review before automation ever touches a real browser session — same
"independent reviewer" principle as Test Case Validation (Agent 4). Verdict
is APPROVED / rejected-with-fix-list / flagged-for-human (destructive
action). Lint results are counts, not booleans (companion doc Part 2 #6) —
one XPath fallback out of twelve selectors is a different risk than an
all-XPath script.

Phase 0 stub.
"""

# TODO (Phase 1): class AutomationReviewAgent(BaseAgent): ...
