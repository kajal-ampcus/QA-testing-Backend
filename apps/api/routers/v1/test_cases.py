"""
Test case endpoints — Test Design Agent (Agent 3) output.
Includes generation trigger, manual drafts, edits, deletion, and submission
to the approval gate (decided through /approvals like requirements).
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from apps.api.routers.v1.approvals import _test_case_side_effect
from core.agents.test_case_validation.agent import TestCaseValidationAgent
from core.agents.test_design.agent import TestDesignAgent
from core.agents.test_design.schemas import TestCaseSpec, TestStep
from domain.enums import ApprovalStatus, RequirementStatus, TestCaseStatus
from infra.db.models.agent_run import AgentRun
from infra.db.models.approval import Approval
from infra.db.models.project import Project
from infra.db.repositories.application_map_repo import ApplicationMapRepository
from infra.db.repositories.requirement_repo import RequirementRepository
from infra.db.repositories.test_case_repo import TestCaseRepository
from schemas.envelope import AgentInputEnvelope, AgentOutputEnvelope, AgentRunStatus

router = APIRouter(prefix="/test-cases", tags=["test-cases"])


# ── Request / Response schemas ────────────────────────────────────────────────

class GenerateTestCasesRequest(BaseModel):
    requirement_id: uuid.UUID
    application_map_id: uuid.UUID | None = None  # defaults to latest usable map
    target_categories: dict[str, list[str]] | None = None
    generation_scope: Literal["all", "ungenerated"] = "all"
    selected_area_ids: list[str] = Field(default_factory=list)
    selected_module_ids: list[str] = Field(default_factory=list)
    selected_branch_keys: list[str] = Field(default_factory=list, max_length=50)


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


class TestCaseRevisionRequest(BaseModel):
    """A tester edit. Recorded as a new version; the case returns to DRAFT."""

    expected_version: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=200)
    objective: str = Field(min_length=3, max_length=800)
    expected_result: str = Field(min_length=3, max_length=2000)
    category: Literal["POSITIVE", "NEGATIVE", "EDGE_CASE"]
    traceability: list[str] = Field(min_length=1, max_length=20)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[TestStep] = Field(min_length=1, max_length=20)
    test_data: dict[str, Any] = Field(default_factory=dict)


class SubmitTestCaseRequest(BaseModel):
    expected_version: int = Field(ge=1)


class BulkReviewRequest(BaseModel):
    decided_by: str = Field(min_length=1, max_length=200)
    test_case_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class BulkReviewFailure(BaseModel):
    test_case_id: uuid.UUID
    tc_code: str | None = None
    error: str


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


class BulkReviewResponse(BaseModel):
    approved: int
    skipped: int
    failed: list[BulkReviewFailure]
    test_cases: list[TestCaseOut]


class GenerateTestCasesResponse(BaseModel):
    generated: int
    test_cases: list[TestCaseOut]
    uncovered_acs: list[str]
    partial_pairing_acs: list[str] = []
    pairing_gaps: list[dict[str, Any]] = []
    needs_review_test_cases: list[str] = []
    duplicates_skipped: int = 0
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


async def _pending_test_case_approval(db: AsyncSession, test_case_id: uuid.UUID) -> Approval | None:
    result = await db.execute(
        select(Approval).where(
            Approval.target_type == "test_case",
            Approval.target_id == test_case_id,
            Approval.status == ApprovalStatus.PENDING,
        )
    )
    return result.scalar_one_or_none()


async def _submit_draft_for_approval(db: AsyncSession, test_case) -> Approval:
    if test_case.status not in {TestCaseStatus.DRAFT, TestCaseStatus.REJECTED}:
        raise HTTPException(
            status_code=409,
            detail=f"Only DRAFT or REJECTED cases can be submitted (is {test_case.status})",
        )
    requirement_pair = await RequirementRepository(db).get_with_current_version(
        test_case.requirement_id
    )
    if requirement_pair is None or requirement_pair[0].status != RequirementStatus.APPROVED:
        raise HTTPException(status_code=409, detail="Approve the requirement before this test case")
    if test_case.requirement_version != requirement_pair[0].current_version:
        raise HTTPException(
            status_code=409,
            detail="The requirement changed after this case was written; edit or regenerate it first",
        )
    tc_repo = TestCaseRepository(db)
    await tc_repo.supersede_pending_approvals(test_case.id, "Superseded by a new submission")
    approval = Approval(
        project_id=test_case.project_id,
        target_type="test_case",
        target_id=test_case.id,
        target_version=test_case.current_version,
        status=ApprovalStatus.PENDING,
    )
    db.add(approval)
    test_case.status = TestCaseStatus.PENDING_APPROVAL
    await db.flush()
    return approval


async def _approve_test_case_approval(
    db: AsyncSession, approval: Approval, decided_by: str
) -> None:
    await _test_case_side_effect(db, approval, ApprovalStatus.APPROVED)
    approval.status = ApprovalStatus.APPROVED
    approval.decided_by = decided_by
    approval.reason = None
    approval.decided_at = datetime.now(UTC)


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
    project = await db.get(Project, project_id)
    if project is None:
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
            "selected_branch_keys": body.selected_branch_keys,
            **({"credential_ref": project.credential_ref} if project.credential_ref else {}),
        },
    )

    try:
        result = await agent.run(envelope_input)
    except Exception as exc:
        if isinstance(exc, ValueError):
            # Input/state problems (requirement not approved, no map, ...).
            error = HTTPException(status_code=422, detail=str(exc))
        elif isinstance(exc, RuntimeError):
            # LLM call failed after retries/schema-repair (infra/llm/*) — a real
            # upstream failure, not a client input problem, so 502 not 422/500.
            error = HTTPException(status_code=502, detail=f"Test case generation failed: {exc}")
        else:
            # SDK-level errors that aren't wrapped as RuntimeError, e.g. an
            # unknown model or a bad API key: provider-config problems.
            error = HTTPException(
                status_code=502,
                detail=(
                    f"LLM provider error ({type(exc).__name__}): {exc}. "
                    "Check LLM_PROVIDER, LLM_MODEL, LLM_BASE_URL, and LLM_API_KEY in .env."
                ),
            )
        await db.rollback()
        _record_run(
            db,
            envelope_input,
            AgentOutputEnvelope(
                agent_run_id=envelope_input.agent_run_id,
                status=AgentRunStatus.FAILED,
                errors=[str(error.detail)[:2000]],
            ),
        )
        await db.commit()
        raise error from exc

    _record_run(db, envelope_input, result.envelope)
    await db.commit()

    # Only the cases this run created; earlier drafts are available from the list endpoint.
    created_ids = {uuid.UUID(ref.id) for ref in result.envelope.artifacts if ref.type == "test_case"}
    pairs = await tc_repo.list_for_requirement(body.requirement_id)
    test_case_outs = [_to_out(tc, v) for tc, v in pairs if tc.id in created_ids]

    return GenerateTestCasesResponse(
        generated=len(test_case_outs),
        test_cases=test_case_outs,
        uncovered_acs=result.uncovered_acs,
        partial_pairing_acs=result.partial_pairing_acs,
        pairing_gaps=result.pairing_gaps,
        needs_review_test_cases=result.needs_review_test_cases,
        duplicates_skipped=result.duplicates_skipped,
        token_usage=result.envelope.token_usage,
    )


def _record_run(
    db: AsyncSession, request: AgentInputEnvelope, output: AgentOutputEnvelope
) -> None:
    db.add(
        AgentRun(
            id=request.agent_run_id,
            project_id=request.project_id,
            agent_name=TestDesignAgent.name,
            status=output.status,
            input_envelope=request.model_dump(mode="json"),
            output_envelope=output.model_dump(mode="json"),
            finished_at=datetime.now(UTC),
        )
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


@router.post("/projects/{project_id}/bulk-review", response_model=BulkReviewResponse)
async def bulk_review_test_cases(
    project_id: uuid.UUID,
    body: BulkReviewRequest,
    db: AsyncSession = Depends(get_db_session),
) -> BulkReviewResponse:
    """Submit and approve selected test cases in one reviewer pass."""
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    decided_by = body.decided_by.strip()
    if not decided_by:
        raise HTTPException(status_code=422, detail="decided_by must not be blank")

    tc_repo = TestCaseRepository(db)
    approved: list[TestCaseOut] = []
    failed: list[BulkReviewFailure] = []
    skipped = 0
    seen: set[uuid.UUID] = set()

    for test_case_id in body.test_case_ids:
        if test_case_id in seen:
            continue
        seen.add(test_case_id)
        pair = await tc_repo.get_with_current_version(test_case_id, for_update=True)
        if pair is None or pair[0].project_id != project_id:
            failed.append(
                BulkReviewFailure(
                    test_case_id=test_case_id,
                    error="Test case not found",
                )
            )
            continue
        test_case, _version = pair
        if test_case.status == TestCaseStatus.APPROVED:
            skipped += 1
            continue
        try:
            if test_case.status in {TestCaseStatus.DRAFT, TestCaseStatus.REJECTED}:
                approval = await _submit_draft_for_approval(db, test_case)
            elif test_case.status == TestCaseStatus.PENDING_APPROVAL:
                approval = await _pending_test_case_approval(db, test_case.id)
                if approval is None:
                    raise HTTPException(
                        status_code=409,
                        detail="No pending approval exists for this test case",
                    )
            else:
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot approve a {test_case.status} test case",
                )
            await _approve_test_case_approval(db, approval, decided_by)
            await db.flush()
            current = await tc_repo.get_with_current_version(test_case.id)
            if current:
                approved.append(_to_out(*current))
        except HTTPException as exc:
            detail = exc.detail
            failed.append(
                BulkReviewFailure(
                    test_case_id=test_case.id,
                    tc_code=test_case.tc_code,
                    error=detail if isinstance(detail, str) else str(detail),
                )
            )

    await db.commit()
    return BulkReviewResponse(
        approved=len(approved),
        skipped=skipped,
        failed=failed,
        test_cases=approved,
    )


@router.post("/projects/{project_id}", response_model=TestCaseOut)
async def create_test_case(
    project_id: uuid.UUID,
    body: CreateTestCaseRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TestCaseOut:
    """Save a tester-authored draft, including edge cases the generator missed."""
    project = await db.get(Project, project_id)
    if project is None:
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
    if requirement.status == RequirementStatus.REJECTED:
        raise HTTPException(status_code=409, detail="Requirement was rejected; revise it first")

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
        credential_ref=project.credential_ref,
    )
    out = _to_out(test_case, current)
    await db.commit()
    return out


async def _load_for_change(
    tc_repo: TestCaseRepository, test_case_id: uuid.UUID, expected_version: int
):
    pair = await tc_repo.get_with_current_version(test_case_id, for_update=True)
    if pair is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    if pair[0].current_version != expected_version:
        raise HTTPException(status_code=409, detail="Test case version changed; reload it first")
    return pair


@router.post("/{test_case_id}/revisions", response_model=TestCaseOut, status_code=201)
async def revise_test_case(
    test_case_id: uuid.UUID,
    body: TestCaseRevisionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TestCaseOut:
    """Edit a draft. The edit is checked against the application map and the
    requirement's current acceptance criteria, and re-bases an OUTDATED case
    onto the current requirement version."""
    tc_repo = TestCaseRepository(db)
    test_case, _ = await _load_for_change(tc_repo, test_case_id, body.expected_version)
    if test_case.status == TestCaseStatus.APPROVED:
        raise HTTPException(
            status_code=409, detail="Approved test cases are immutable; create a new draft instead"
        )
    requirement_pair = await RequirementRepository(db).get_with_current_version(
        test_case.requirement_id
    )
    app_map = await ApplicationMapRepository(db).get_with_states(test_case.application_map_id)
    if requirement_pair is None or app_map is None:
        raise HTTPException(status_code=409, detail="Requirement or application map no longer exists")
    requirement, requirement_version = requirement_pair
    ac_ids = {str(ac["id"]) for ac in requirement_version.acceptance_criteria if ac.get("id")}
    steps = [
        step.model_copy(update={"step_number": index})
        for index, step in enumerate(body.steps, start=1)
    ]
    version_data: dict[str, Any] = {
        "title": body.title.strip(),
        "objective": body.objective.strip(),
        "preconditions": [item.strip() for item in body.preconditions if item.strip()],
        "steps": [step.model_dump(mode="json") for step in steps],
        "expected_result": body.expected_result.strip(),
        "test_data": body.test_data,
        "traceability": body.traceability,
        "category": body.category,
        "confidence": 1.0,
    }
    try:
        spec = TestCaseSpec.model_validate(version_data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    map_states = [
        {"state_code": s.state_code, "url_pattern": s.url_pattern, "elements": s.elements}
        for s in app_map.states
    ]
    issues = TestCaseValidationAgent().issues_for(spec, map_states, ac_ids)
    if issues:
        raise HTTPException(status_code=422, detail=issues)
    version = await tc_repo.append_version(test_case, version_data)
    test_case.requirement_version = requirement.current_version
    await db.commit()
    return _to_out(test_case, version)


@router.post("/{test_case_id}/submit", response_model=TestCaseOut)
async def submit_test_case(
    test_case_id: uuid.UUID,
    body: SubmitTestCaseRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TestCaseOut:
    """Send the current version to the approval gate (decided via /approvals)."""
    tc_repo = TestCaseRepository(db)
    test_case, version = await _load_for_change(tc_repo, test_case_id, body.expected_version)
    await _submit_draft_for_approval(db, test_case)
    await db.commit()
    return _to_out(test_case, version)


@router.delete("/{test_case_id}", status_code=204)
async def delete_test_case(
    test_case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    tc_repo = TestCaseRepository(db)
    pair = await tc_repo.get_with_current_version(test_case_id, for_update=True)
    if pair is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    if pair[0].status == TestCaseStatus.APPROVED:
        raise HTTPException(status_code=409, detail="Approved test cases cannot be deleted")
    await tc_repo.delete(pair[0])
    await db.commit()
    return Response(status_code=204)


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
