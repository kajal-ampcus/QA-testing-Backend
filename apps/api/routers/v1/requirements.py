"""
Requirement endpoints — triggers the Requirement Understanding Agent and
creates the first human-approval gate (Section 21). Milestone 1's
orchestrator stand-in: core/orchestrator/ is still a stub, so this router
plays the role of "call the agent, persist the result, create the approval
row" as one transaction. That orchestration logic should move into
core/orchestrator/ once later milestones need it to coordinate more than one
agent — flagged here so it isn't forgotten.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from core.agents.requirement_understanding.agent import RequirementUnderstandingAgent
from core.agents.requirement_understanding.schemas import RequirementUnderstandingResult
from domain.enums import ApprovalStatus, RequirementStatus
from infra.db.models.agent_run import AgentRun
from infra.db.models.approval import Approval
from infra.db.models.project import Project
from infra.db.models.requirement import Requirement, RequirementVersion
from infra.db.repositories.requirement_repo import RequirementRepository
from schemas.envelope import (
    AgentArtifactRef,
    AgentInputEnvelope,
    AgentOutputEnvelope,
    AgentRunStatus,
)
from schemas.requirement import (
    RequirementClarificationRequest,
    RequirementCreateRequest,
    RequirementResponse,
    RequirementRevisionRequest,
)

router = APIRouter(prefix="/requirements", tags=["requirements"])


def _to_response(requirement: Requirement, version: RequirementVersion) -> RequirementResponse:
    return RequirementResponse(
        id=requirement.id,
        req_code=requirement.req_code,
        project_id=requirement.project_id,
        version=version.version,
        status=requirement.status,
        title=version.title,
        description=version.description,
        acceptance_criteria=version.acceptance_criteria,
        ambiguities=version.ambiguities,
        domain_tags=version.domain_tags,
    )


async def _extract_requirement(
    session: AsyncSession, project_id: uuid.UUID, raw_text: str
) -> tuple[RequirementUnderstandingAgent, AgentInputEnvelope, RequirementUnderstandingResult]:
    agent = RequirementUnderstandingAgent()
    input_envelope = AgentInputEnvelope(
        agent_run_id=uuid.uuid4(),
        project_id=project_id,
        trigger="manual",
        payload={"raw_text": raw_text, "project_glossary": {}},
    )
    try:
        result = await agent.run(input_envelope)
    except Exception as exc:  # noqa: BLE001 - persist failed agent calls without exposing provider data
        failure = AgentOutputEnvelope(
            agent_run_id=input_envelope.agent_run_id,
            status=AgentRunStatus.FAILED,
            errors=[f"{type(exc).__name__}: requirement extraction failed"],
        )
        session.add(
            AgentRun(
                id=input_envelope.agent_run_id,
                project_id=project_id,
                agent_name=agent.name,
                status=failure.status,
                input_envelope=input_envelope.model_dump(mode="json"),
                output_envelope=failure.model_dump(mode="json"),
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        raise HTTPException(
            status_code=502, detail="Requirement extraction failed; check LLM tool compatibility"
        ) from exc
    return agent, input_envelope, result


def _record_successful_run(
    session: AsyncSession,
    agent: RequirementUnderstandingAgent,
    input_envelope: AgentInputEnvelope,
    result: RequirementUnderstandingResult,
    requirement: Requirement,
    version: RequirementVersion,
) -> None:
    result.envelope.artifacts = [
        AgentArtifactRef(type="requirement", id=str(requirement.id), version=version.version)
    ]
    session.add(
        AgentRun(
            id=input_envelope.agent_run_id,
            project_id=requirement.project_id,
            agent_name=agent.name,
            status=result.envelope.status,
            input_envelope=input_envelope.model_dump(mode="json"),
            output_envelope=result.envelope.model_dump(mode="json"),
            finished_at=datetime.now(UTC),
        )
    )


def _request_approval(session: AsyncSession, requirement: Requirement) -> None:
    session.add(
        Approval(
            project_id=requirement.project_id,
            target_type="requirement",
            target_id=requirement.id,
            status=ApprovalStatus.PENDING,
        )
    )


async def _supersede_pending_approvals(session: AsyncSession, requirement_id: uuid.UUID) -> None:
    pending = await session.execute(
        select(Approval).where(
            Approval.target_type == "requirement",
            Approval.target_id == requirement_id,
            Approval.status == ApprovalStatus.PENDING,
        )
    )
    for approval in pending.scalars():
        approval.status = ApprovalStatus.REJECTED
        approval.decided_by = "system"
        approval.reason = "Superseded by a new requirement version"
        approval.decided_at = datetime.now(UTC)


@router.post("/projects/{project_id}", response_model=RequirementResponse, status_code=201)
async def create_requirement(
    project_id: uuid.UUID,
    body: RequirementCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> RequirementResponse:
    repo = RequirementRepository(session)
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    agent, input_envelope, result = await _extract_requirement(session, project_id, body.raw_text)
    req_code = await repo.next_req_code(project_id)

    status = (
        RequirementStatus.NEEDS_CLARIFICATION
        if result.envelope.status == "PARTIAL"
        else RequirementStatus.PENDING_APPROVAL
    )

    requirement, version = await repo.create(
        project_id=project_id,
        req_code=req_code,
        raw_text=body.raw_text,
        extraction=result.extraction,
        status=status,
    )

    _record_successful_run(session, agent, input_envelope, result, requirement, version)

    # Human-approval gate (Section 21) — created even for NEEDS_CLARIFICATION
    # requirements, since a tester approving/editing is exactly how
    # clarification gets resolved.
    _request_approval(session, requirement)

    await session.commit()
    return _to_response(requirement, version)


@router.post("/{requirement_id}/revisions", response_model=RequirementResponse, status_code=201)
async def revise_requirement(
    requirement_id: uuid.UUID,
    body: RequirementRevisionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> RequirementResponse:
    requirement = await session.get(Requirement, requirement_id)
    if requirement is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    agent, input_envelope, result = await _extract_requirement(
        session, requirement.project_id, body.raw_text
    )
    locked = await session.execute(
        select(Requirement).where(Requirement.id == requirement_id).with_for_update()
    )
    requirement = locked.scalar_one()
    repo = RequirementRepository(session)
    version = await repo.append_version(requirement, body.raw_text, result.extraction)
    requirement.status = (
        RequirementStatus.NEEDS_CLARIFICATION
        if result.envelope.status == AgentRunStatus.PARTIAL
        else RequirementStatus.PENDING_APPROVAL
    )
    await _supersede_pending_approvals(session, requirement_id)
    _record_successful_run(session, agent, input_envelope, result, requirement, version)
    _request_approval(session, requirement)
    await session.commit()
    return _to_response(requirement, version)


@router.post(
    "/{requirement_id}/clarifications", response_model=RequirementResponse, status_code=201
)
async def clarify_requirement(
    requirement_id: uuid.UUID,
    body: RequirementClarificationRequest,
    session: AsyncSession = Depends(get_db_session),
) -> RequirementResponse:
    locked = await session.execute(
        select(Requirement).where(Requirement.id == requirement_id).with_for_update()
    )
    requirement = locked.scalar_one_or_none()
    if requirement is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    if requirement.current_version != body.expected_version:
        raise HTTPException(status_code=409, detail="Requirement version changed; reload it first")
    current_result = await session.execute(
        select(RequirementVersion).where(
            RequirementVersion.requirement_id == requirement_id,
            RequirementVersion.version == requirement.current_version,
        )
    )
    current = current_result.scalar_one()
    if not current.ambiguities:
        raise HTTPException(status_code=409, detail="Current requirement has no ambiguities")
    resolved_by = body.resolved_by.strip()
    if not resolved_by or any(not item.decision.strip() for item in body.resolutions):
        raise HTTPException(status_code=422, detail="Resolved by and decisions must not be blank")
    indexes = [item.ambiguity_index for item in body.resolutions]
    if len(indexes) != len(set(indexes)) or set(indexes) != set(range(len(current.ambiguities))):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one decision for every current ambiguity index",
        )
    resolutions = [
        (current.ambiguities[item.ambiguity_index], item.decision.strip())
        for item in sorted(body.resolutions, key=lambda item: item.ambiguity_index)
    ]
    repo = RequirementRepository(session)
    version = await repo.append_clarified_version(requirement, current, resolutions, resolved_by)
    requirement.status = RequirementStatus.PENDING_APPROVAL
    await _supersede_pending_approvals(session, requirement_id)
    _request_approval(session, requirement)
    await session.commit()
    return _to_response(requirement, version)


@router.get("/projects/{project_id}", response_model=list[RequirementResponse])
async def list_requirements(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> list[RequirementResponse]:
    repo = RequirementRepository(session)
    requirements = await repo.list_for_project(project_id)
    responses = []
    for requirement in requirements:
        pair = await repo.get_with_current_version(requirement.id)
        if pair is not None:
            responses.append(_to_response(*pair))
    return responses


@router.get("/{requirement_id}", response_model=RequirementResponse)
async def get_requirement(
    requirement_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> RequirementResponse:
    repo = RequirementRepository(session)
    pair = await repo.get_with_current_version(requirement_id)
    if pair is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return _to_response(*pair)
