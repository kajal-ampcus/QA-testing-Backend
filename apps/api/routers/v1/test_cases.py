"""
Test case endpoints — Test Design Agent (Agent 3) output.
Includes generation trigger, list, get, and approval gate.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
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
    application_map_id: uuid.UUID | None = None  # defaults to latest usable map
    target_categories: dict[str, list[str]] | None = None
    generation_scope: Literal["all", "ungenerated"] = "all"
    selected_area_ids: list[str] = Field(default_factory=list)
    selected_module_ids: list[str] = Field(default_factory=list)


class CreateTestCaseRequest(BaseModel):
    """Manual draft — typically an EDGE_CASE the generator did not cover."""

    requirement_id: uuid.UUID
    application_map_id: uuid.UUID | None = None
    title: str = Field(min_length=3, max_length=200)
    objective: str = Field(min_length=3, max_length=800)
    expected_result: str = Field(min_length=3, max_length=2000)
    category: Literal["POSITIVE", "NEGATIVE", "EDGE_CASE"] = "EDGE_CASE"
    traceability: list[str] = Field(min_length=1, max_length=20)
    preconditions: list[str] = Field(default_factory=list)
    step_notes: list[str] = Field(
        default_factory=list,
        description="Optional tester notes; each line becomes a step after navigate.",
    )
    start_state_code: str | None = None


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


def _to_out(tc, version) -> TestCaseOut:
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
            version=version.version,
            title=version.title,
            objective=version.objective,
            category=version.category,
            preconditions=version.preconditions,
            steps=version.steps,
            expected_result=version.expected_result,
            test_data=version.test_data,
            traceability=version.traceability,
            confidence=version.confidence,
        ),
    )


def _manual_steps(
    *,
    state_code: str,
    expected_result: str,
    step_notes: list[str],
) -> list[dict]:
    notes = [note.strip() for note in step_notes if note.strip()]
    steps: list[dict] = [
        {
            "step_number": 1,
            "action": "navigate",
            "target": {"state_code": state_code},
            "value": None,
            "expected": None,
        }
    ]
    if not notes:
        steps.append(
            {
                "step_number": 2,
                "action": "assert",
                "target": {"state_code": state_code},
                "value": None,
                "expected": expected_result,
            }
        )
        return steps
    for index, note in enumerate(notes, start=2):
        steps.append(
            {
                "step_number": index,
                "action": "assert",
                "target": {"state_code": state_code},
                "value": None,
                "expected": note,
            }
        )
    return steps


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
      2. Load latest COMPLETE or PARTIAL application map (or pinned map_id)
      3. Call LLM with structured tool use → list of TestCaseSpec
      4. Validate AC coverage (warn but don't block if some ACs uncovered)
      5. Persist each test case as TestCase + TestCaseVersion (status=DRAFT)
      6. Return all generated test cases

    Requires:
      - requirement must be APPROVED
      - application map must be COMPLETE or contain usable PARTIAL observations
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
            **({"target_categories": body.target_categories} if body.target_categories else {}),
            "generation_scope": body.generation_scope,
            "selected_area_ids": body.selected_area_ids,
            "selected_module_ids": body.selected_module_ids,
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
    test_case_outs = [_to_out(tc, v) for tc, v in pairs]

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
    return [_to_out(tc, v) for tc, v in pairs]


@router.post("/projects/{project_id}", response_model=TestCaseOut)
async def create_test_case(
    project_id: uuid.UUID,
    body: CreateTestCaseRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TestCaseOut:
    """Save a tester-authored draft, including edge cases the generator missed."""
    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    req_repo = RequirementRepository(db)
    map_repo = ApplicationMapRepository(db)
    tc_repo = TestCaseRepository(db)

    pair = await req_repo.get_with_current_version(body.requirement_id)
    if pair is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    requirement, version = pair
    if requirement.project_id != project_id:
        raise HTTPException(status_code=404, detail="Requirement not found")

    ac_ids = {str(item.get("id")) for item in (version.acceptance_criteria or []) if item.get("id")}
    unknown = [ac_id for ac_id in body.traceability if ac_id not in ac_ids]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown acceptance criteria: {', '.join(unknown)}",
        )

    app_map = (
        await map_repo.get_with_states(body.application_map_id)
        if body.application_map_id
        else await map_repo.get_latest_for_project(project_id)
    )
    if app_map is None or app_map.project_id != project_id:
        raise HTTPException(
            status_code=422,
            detail="Discover the application first so this case can be grounded to a map.",
        )
    state_codes = [state.state_code for state in app_map.states]
    if not state_codes:
        raise HTTPException(
            status_code=422,
            detail="The application map has no observed states yet.",
        )
    start_state = body.start_state_code or state_codes[0]
    if start_state not in set(state_codes):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown start state: {start_state}",
        )

    preconditions = [item.strip() for item in body.preconditions if item.strip()]
    tc_code = await tc_repo.next_tc_code(project_id)
    test_case, current = await tc_repo.create(
        project_id=project_id,
        tc_code=tc_code,
        requirement_id=requirement.id,
        requirement_version=requirement.current_version,
        application_map_id=app_map.id,
        version_data={
            "title": body.title.strip(),
            "objective": body.objective.strip(),
            "preconditions": preconditions,
            "steps": _manual_steps(
                state_code=start_state,
                expected_result=body.expected_result.strip(),
                step_notes=body.step_notes,
            ),
            "expected_result": body.expected_result.strip(),
            "test_data": {"source": "manual"},
            "traceability": body.traceability,
            "category": body.category,
            "confidence": 1.0,
        },
    )
    out = _to_out(test_case, current)
    await db.commit()
    return out


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
    return _to_out(*pair)
