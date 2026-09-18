"""
Structured output contract for Requirement Understanding — this is the exact
shape the LLM is forced to return via tool use (infra/llm/anthropic_client.py).
Matches companion doc Part 2 #1.
"""

from pydantic import BaseModel, Field

from domain.enums import EvidenceSource
from schemas.envelope import AgentOutputEnvelope


class AcceptanceCriterion(BaseModel):
    id: str = Field(description="Short local id, e.g. 'AC-1'")
    text: str
    source: EvidenceSource = Field(
        description="REQUIREMENT if stated explicitly in the tester's text, INFERENCE if you derived it"
    )


class Ambiguity(BaseModel):
    field: str = Field(description="Which part is ambiguous, e.g. 'acceptance_criteria[2]'")
    issue: str
    requires_clarification: bool = True


class RequirementExtraction(BaseModel):
    """What the model must return. A non-empty `ambiguities` list means the
    requirement is NEEDS_CLARIFICATION, not silently resolved."""

    title: str = Field(description="Short title, a few words")
    description: str = Field(description="Normalized, paraphrased description of the requirement")
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    domain_tags: list[str] = Field(default_factory=list, description="e.g. ['auth', 'payment']")


class RequirementUnderstandingResult(BaseModel):
    """Agent.run()'s actual return type: the generic envelope plus the
    domain-specific extraction the caller needs to persist. Keeping these
    separate means AgentOutputEnvelope stays the same shape for every agent
    (docs/PROJECT_STRUCTURE.md point 7) while still giving the router
    something concrete to write to the database."""

    envelope: AgentOutputEnvelope
    extraction: RequirementExtraction
