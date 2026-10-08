"""
Structured output contract for Test Design Agent.
The LLM is forced to return a list of TestCaseSpec objects via tool use.
Each TestCaseSpec maps exactly to one TestCaseVersion row.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from schemas.envelope import AgentOutputEnvelope


class StepTarget(BaseModel):
    """A real nested model (not a free-form dict) so the JSON schema sent to
    the LLM structurally requires state_code as a named property instead of
    only describing it in prose. element_code is intentionally OPTIONAL:
    a `navigate` step's target is a whole state/page (no single element), and
    some `assert` steps check something page-level (e.g. the URL) rather than
    one element — requiring element_code on every step was our own schema
    bug, not a model-compliance failure (the model was correctly omitting it
    for exactly those steps)."""

    state_code: str = Field(description="e.g. STATE-001")
    element_code: str | None = Field(
        default=None,
        description=(
            "e.g. EL-005. REQUIRED for fill/select/click steps and for assert steps "
            "that check one specific element. Omit ONLY for navigate steps "
            "and for assert steps that check something page-level (e.g. the "
            "current URL) rather than one element."
        ),
    )
    element_name: str | None = Field(default=None, description="e.g. 'Email'")
    element_role: str | None = Field(default=None, description="e.g. 'textbox'")

    model_config = {"extra": "forbid"}


class TestStep(BaseModel):
    step_number: int
    action: Literal["navigate", "fill", "select", "click", "assert", "wait"]
    target: StepTarget = Field(
        description="Must reference observed DOM elements only. Never invent a state_code or element_code."
    )
    value: str | None = Field(
        default=None,
        description="For fill actions: the value to type. Use test_data keys like '{valid_email}'.",
    )
    expected: str | None = Field(
        default=None, description="For assert actions: what should be true after this step."
    )

    model_config = {"extra": "forbid"}


class TestCaseSpec(BaseModel):
    title: str = Field(description="Short descriptive title, e.g. 'Login with valid credentials'")
    objective: str = Field(description="One sentence: what this test case verifies")
    category: Literal["POSITIVE", "NEGATIVE", "EDGE_CASE"] = Field(
        description=(
            "The TEST TYPE — must be EXACTLY one of the three literal strings "
            "'POSITIVE', 'NEGATIVE', or 'EDGE_CASE'. This is NOT the feature or "
            "domain area (never 'Authentication', 'Chat Interaction', etc.). "
            "POSITIVE: happy path, everything valid. "
            "NEGATIVE: invalid input or wrong credentials, expects a rejection/error. "
            "EDGE_CASE: boundary values, empty fields, special characters."
        )
    )
    preconditions: list[str] = Field(
        description="What must be true before this test runs, e.g. 'User is on /login page'"
    )
    steps: list[TestStep] = Field(min_length=1, max_length=8)
    expected_result: str = Field(
        description="What the tester should observe after all steps complete"
    )
    test_data: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Named test data sets used in steps. "
            "Keys are data set names (e.g. 'valid_credentials'), "
            "values are dicts of field→value. "
            "For negative tests include invalid variants."
        ),
    )
    traceability: list[str] = Field(
        min_length=1,
        description="Which AC ids from the requirement this test case covers, e.g. ['AC-1', 'AC-2']",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "0.9 if all steps reference observed DOM elements. "
            "0.7 if some steps are inferred. "
            "0.5 if the AC cannot be mapped to any observed state."
        ),
    )

    model_config = {"extra": "forbid"}


class TestCaseBatch(BaseModel):
    """Root object the LLM must return — a list of test case specs."""

    test_cases: list[TestCaseSpec] = Field(
        min_length=1,
        max_length=4,
        description=(
            "Compact batch: at most 2 ACs × one category (POSITIVE or NEGATIVE)."
        ),
    )

    model_config = {"extra": "forbid"}


class TestDesignResult(BaseModel):
    """Agent.run()'s return type: envelope + the generated specs before DB persistence."""

    envelope: AgentOutputEnvelope
    test_cases: list[TestCaseSpec]
    uncovered_acs: list[str] = Field(default_factory=list)
    partial_pairing_acs: list[str] = Field(
        default_factory=list,
        description="ACs that have at least one test case but are missing a POSITIVE or NEGATIVE counterpart (RULE 2).",
    )
    pairing_gaps: list[dict[str, Any]] = Field(
        default_factory=list,
        description='Structured form of partial_pairing_acs: [{"ac_id": "AC-1", "missing": ["NEGATIVE"]}].',
    )
    needs_review_test_cases: list[str] = Field(
        default_factory=list,
        description="Test case titles below the confidence threshold, usually referencing UI not yet in the application map.",
    )
    duplicates_skipped: int = 0
