"""
API-facing pydantic schemas for Requirement — the wire format, distinct from
domain/entities/requirement.py (pure domain object) and
infra/db/models/requirement.py (ORM), per docs/PROJECT_STRUCTURE.md point 6.
"""

import uuid

from pydantic import BaseModel, Field


class RequirementCreateRequest(BaseModel):
    raw_text: str = Field(min_length=1)
    external_ref: str | None = None


class RequirementRevisionRequest(BaseModel):
    raw_text: str = Field(min_length=1)


class AmbiguityResolution(BaseModel):
    ambiguity_index: int = Field(ge=0)
    decision: str = Field(min_length=1)


class RequirementClarificationRequest(BaseModel):
    expected_version: int = Field(ge=1)
    resolved_by: str = Field(min_length=1)
    resolutions: list[AmbiguityResolution] = Field(min_length=1)


class AcceptanceCriterionOut(BaseModel):
    id: str
    text: str
    source: str


class AmbiguityOut(BaseModel):
    field: str
    issue: str
    requires_clarification: bool


class RequirementResponse(BaseModel):
    id: uuid.UUID
    req_code: str
    project_id: uuid.UUID
    version: int
    status: str
    title: str
    description: str
    acceptance_criteria: list[AcceptanceCriterionOut]
    ambiguities: list[AmbiguityOut]
    domain_tags: list[str]
