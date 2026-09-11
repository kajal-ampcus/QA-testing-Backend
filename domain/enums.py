"""
Shared enums used across domain entities, schemas, and infra/db/models
(docs/PROJECT_STRUCTURE.md point 6 — domain has no ORM/pydantic coupling, but
enums are pure Python and safe to share everywhere).

Phase 0 stub — key enums to define in Phase 1:

- EvidenceSource: REQUIREMENT | OBSERVED_DOM | OBSERVED_NETWORK |
  OBSERVED_CONSOLE | OBSERVED_UI | TEST_EXECUTION | INFERENCE | UNKNOWN
  (architecture doc Section 30)
- FailureClassification: see core/agents/failure_analysis/classification_taxonomy.py
  (the 11-value taxonomy, Section 16)
- ConfidenceBand: HIGH (>=0.90) | REVIEW (0.70-0.89) | BLOCK (<0.70) (Section 22)
- ApprovalStatus: PENDING | APPROVED | REJECTED
- OrchestratorState: see core/orchestrator/state_machine.py (Section 25)
- RiskLevel: SAFE | REVIEW | DESTRUCTIVE (Section 29)
"""
