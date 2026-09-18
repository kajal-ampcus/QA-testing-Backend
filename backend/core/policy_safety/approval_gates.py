"""
The structural implementation of every human-approval gate in Section 21:
requirement interpretation, validated test cases, destructive/production
automation, defect filing. This module — not any individual agent — decides
whether a given action requires approval before proceeding, and the
orchestrator consults it at each relevant transition (Section 25).

Deliberately NOT an LLM agent (Section 5: Safety/Policy was demoted from
"agent" to infrastructure specifically so it can't be prompt-injected
around) and deliberately not nested under core/agents/
(docs/PROJECT_STRUCTURE.md point 5).

Phase 0 stub.
"""

# TODO (Phase 1): def requires_approval(action_type, risk_level, environment) -> bool: ...
