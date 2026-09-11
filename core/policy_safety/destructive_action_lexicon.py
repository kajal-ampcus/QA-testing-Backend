"""
Destructive-action lexicon (architecture doc Section 29): matches accessible
name/label against terms like delete, remove, cancel subscription, pay,
submit payment, deactivate — plus endpoint-pattern matching for mutation
calls on "risky" resources. Elements matching this are tagged DESTRUCTIVE at
Discovery time (once, on the element, not re-derived by every downstream
consumer — docs/PROJECT_STRUCTURE.md point 5) and never auto-clicked/
auto-executed without passing through approval_gates.py.

This is deliberately deterministic pattern matching, not an LLM call —
safety classification must be non-bypassable and cannot itself be
prompt-injected.

Phase 0 stub.
"""

# TODO (Phase 1): DESTRUCTIVE_TERMS = [...]; def classify(element) -> RiskLevel: ...
