"""
Test case endpoints — Test Design Agent (Agent 3) output.
Includes generation trigger, list, get, and approval gate.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from core.agents.test_design.agent import TestDesignAgent
from infra.db.models.project import Project
from infra.db.repositories.application_map_repo import ApplicationMapRepository
from infra.db.repositories.requirement_repo import RequirementRepository
from infra.db.repositories.test_case_repo import TestCaseRepository
from schemas.envelope import AgentInputEnvelope

router = APIRouter(prefix="/test-cases", tags=["test-cases"])


# ── Request / Response schemas ────────────────────────────────────────────────

class GenerateTestCasesRequest(BaseModel):
    requirement_id: uuid.UUID
    application_map_id: uuid.UUID | None = None  # defaults to latest COMPLETE map


class TestStepOut(BaseModel):
    step_number: int
    action: str
    target: dict
    value: str | None = None
    expected: str | None = None


class TestCaseVersionOut(BaseModel):
    version: int
    title: str
    objective: str
    category: str
    preconditions: list[str]
    steps: list[dict]
    expected_result: str
    test_data: dict
    traceability: list[str]
    confidence: float


class TestCaseOut(BaseModel):
    id: uuid.UUID
    tc_code: str
    project_id: uuid.UUID
    requirement_id: uuid.UUID
    requirement_version: int
    application_map_id: uuid.UUID
    status: str
    current_version: int
    current: TestCaseVersionOut


class GenerateTestCasesResponse(BaseModel):
    generated: int
    test_cases: list[TestCaseOut]
    uncovered_acs: list[str]
    partial_pairing_acs: list[str] = []
    needs_review_test_cases: list[str] = []
    token_usage: dict | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/generate", response_model=GenerateTestCasesResponse)
async def generate_test_cases(
    project_id: uuid.UUID,
    body: GenerateTestCasesRequest,
    db: AsyncSession = Depends(get_db_session),
) -> GenerateTestCasesResponse:
    """
    Trigger Test Design Agent (Agent 3) for one approved requirement.

    Flow:
      1. Load approved requirement + its current version (ACs, description)
      2. Load latest COMPLETE application map (or pinned map_id)
      3. Call LLM with structured tool use → list of TestCaseSpec
      4. Validate AC coverage (warn but don't block if some ACs uncovered)
      5. Persist each test case as TestCase + TestCaseVersion (status=DRAFT)
      6. Return all generated test cases

    Requires:
      - requirement must be APPROVED
      - application map must be COMPLETE
    """
    req_repo = RequirementRepository(db)
    map_repo = ApplicationMapRepository(db)
    tc_repo = TestCaseRepository(db)

    # Fail fast with a clean 404 if project_id doesn't exist — without this,
    # a wrong/stale project_id sails all the way through requirement lookup
    # and the (paid) LLM call, then only fails as an opaque Postgres FK
    # violation when the generated test cases are inserted.
    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    agent = TestDesignAgent(
        requirement_repo=req_repo,
        map_repo=map_repo,
        test_case_repo=tc_repo,
    )

    envelope_input = AgentInputEnvelope(
        agent_run_id=uuid.uuid4(),
        project_id=project_id,
        trigger="manual",
        payload={
            "requirement_id": str(body.requirement_id),
            **({"application_map_id": str(body.application_map_id)} if body.application_map_id else {}),
        },
    )

    try:
        result = await agent.run(envelope_input)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        # LLM call failed after retries/schema-repair (infra/llm/*) — a real
        # upstream failure, not a client input problem, so 502 not 422/500.
        raise HTTPException(status_code=502, detail=f"Test case generation failed: {exc}") from exc
    except Exception as exc:
        # Catch SDK-level errors that aren't wrapped as RuntimeError:
        # e.g. openai.NotFoundError (unknown model), openai.AuthenticationError
        # (bad API key), anthropic.AuthenticationError, etc. These are all
        # provider-config problems, not server bugs — surface as 502 with a
        # meaningful message instead of letting FastAPI emit an opaque 500.
        exc_type = type(exc).__name__
        raise HTTPException(
            status_code=502,
            detail=(
                f"LLM provider error ({exc_type}): {exc}. "
                "Check LLM_PROVIDER, LLM_MODEL, LLM_BASE_URL, and LLM_API_KEY in qa-platform/.env."
            ),
        ) from exc


    # Build response from persisted test cases
    pairs = await tc_repo.list_for_requirement(body.requirement_id)
    test_case_outs = [
        TestCaseOut(
            id=tc.id,
            tc_code=tc.tc_code,
            project_id=tc.project_id,
            requirement_id=tc.requirement_id,
            requirement_version=tc.requirement_version,
            application_map_id=tc.application_map_id,
            status=tc.status,
            current_version=tc.current_version,
            current=TestCaseVersionOut(
                version=v.version,
                title=v.title,
                objective=v.objective,
                category=v.category,
                preconditions=v.preconditions,
                steps=v.steps,
                expected_result=v.expected_result,
                test_data=v.test_data,
                traceability=v.traceability,
                confidence=v.confidence,
            ),
        )
        for tc, v in pairs
    ]

    return GenerateTestCasesResponse(
        generated=len(result.test_cases),
        test_cases=test_case_outs,
        uncovered_acs=result.uncovered_acs,
        partial_pairing_acs=result.partial_pairing_acs,
        needs_review_test_cases=result.needs_review_test_cases,
        token_usage=result.envelope.token_usage,
    )


@router.get("/projects/{project_id}", response_model=list[TestCaseOut])
async def list_test_cases(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> list[TestCaseOut]:
    """List all test cases for a project with their current version."""
    tc_repo = TestCaseRepository(db)
    pairs = await tc_repo.list_for_project(project_id)
    return [
        TestCaseOut(
            id=tc.id,
            tc_code=tc.tc_code,
            project_id=tc.project_id,
            requirement_id=tc.requirement_id,
            requirement_version=tc.requirement_version,
            application_map_id=tc.application_map_id,
            status=tc.status,
            current_version=tc.current_version,
            current=TestCaseVersionOut(
                version=v.version,
                title=v.title,
                objective=v.objective,
                category=v.category,
                preconditions=v.preconditions,
                steps=v.steps,
                expected_result=v.expected_result,
                test_data=v.test_data,
                traceability=v.traceability,
                confidence=v.confidence,
            ),
        )
        for tc, v in pairs
    ]


@router.get("/{test_case_id}", response_model=TestCaseOut)
async def get_test_case(
    test_case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> TestCaseOut:
    """Get a single test case with its current version."""
    tc_repo = TestCaseRepository(db)
    pair = await tc_repo.get_with_current_version(test_case_id)
    if pair is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    tc, v = pair
    return TestCaseOut(
        id=tc.id,
        tc_code=tc.tc_code,
        project_id=tc.project_id,
        requirement_id=tc.requirement_id,
        requirement_version=tc.requirement_version,
        application_map_id=tc.application_map_id,
        status=tc.status,
        current_version=tc.current_version,
        current=TestCaseVersionOut(
            version=v.version,
            title=v.title,
            objective=v.objective,
            category=v.category,
            preconditions=v.preconditions,
            steps=v.steps,
            expected_result=v.expected_result,
            test_data=v.test_data,
            traceability=v.traceability,
            confidence=v.confidence,
        ),
    )
