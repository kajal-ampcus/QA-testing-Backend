"""
API-facing pydantic schemas for Requirement — the wire format, distinct from
domain/entities/requirement.py (pure domain object) and
infra/db/models/requirement.py (ORM), per docs/PROJECT_STRUCTURE.md point 6.
"""

import uuid

from pydantic import BaseModel, Field

MAX_REQUIREMENT_TEXT = 20000


class RequirementCreateRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=MAX_REQUIREMENT_TEXT)
    external_ref: str | None = Field(default=None, max_length=200)


class RequirementRevisionRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=MAX_REQUIREMENT_TEXT)


class AmbiguityResolution(BaseModel):
    ambiguity_index: int = Field(ge=0)
    decision: str = Field(min_length=1)


class RequirementClarificationRequest(BaseModel):
    expected_version: int = Field(ge=1)
    resolved_by: str = Field(min_length=1)
    resolutions: list[AmbiguityResolution] = Field(min_length=1)


class AcceptanceCriterionEdit(BaseModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class AcceptanceCriteriaEditRequest(BaseModel):
    expected_version: int = Field(ge=1)
    items: list[AcceptanceCriterionEdit] = Field(min_length=1)


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
    external_ref: str | None = None
    title: str
    description: str
    acceptance_criteria: list[AcceptanceCriterionOut]
    ambiguities: list[AmbiguityOut]
    domain_tags: list[str]
