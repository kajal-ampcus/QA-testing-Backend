"""
Requirement entity (architecture doc Section 26). Pure domain object — no
ORM/pydantic coupling (docs/PROJECT_STRUCTURE.md point 6). Represented
separately in infra/db/models/requirement.py (persistence) and
schemas/requirement.py (wire format).

Fields (Phase 1): id (REQ-ID), version, title, description,
acceptance_criteria[], domain_tags[], status.

Phase 0 stub.
"""
