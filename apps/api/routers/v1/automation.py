"""
Automation script endpoints — generation, review, inspection, and download.

After the suite is written, this stage queues the Test Execution Agent.
The worker runs npx playwright test. The tester does not run that command.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from apps.api.settings import ApiSettings
from core.agents.automation_generation.agent import AutomationGenerationAgent
from core.agents.automation_generation.artifacts import (
    build_zip,
    generation_dir,
    ide_links,
    list_files,
    read_sources,
)
from core.agents.automation_generation.eligibility import (
    AutomationEligibilityError,
    CaseSnapshot,
    MapSnapshot,
    RequirementSnapshot,
    select_eligible,
)
from core.agents.automation_generation.suite import CaseInput, _forbidden_literals
from core.agents.automation_review.agent import AutomationReviewAgent
from core.policy_safety.approval_gates import requires_approval
from domain.enums import ApprovalStatus, AutomationReviewStatus, RiskLevel
from infra.db.models.agent_run import AgentRun
from infra.db.models.application_map import ApplicationMap
from infra.db.models.approval import Approval
from infra.db.models.automation import AutomationScript
from infra.db.models.project import Project
from infra.db.models.requirement import Requirement
from infra.db.models.test_case import TestCase, TestCaseVersion
from infra.db.repositories.application_map_repo import ApplicationMapRepository
from infra.db.repositories.automation_repo import AutomationRepository
from infra.db.repositories.execution_repo import ExecutionRepository
from infra.db.repositories.test_case_repo import TestCaseRepository
from schemas.automation import (
    AutomationGenerationOut,
    AutomationGenerationSummary,
    AutomationListOut,
    BlockedCaseOut,
    GenerateAutomationRequest,
    LintOut,
    ScriptOut,
    SourceFileOut,
    VerificationOut,
)
from schemas.envelope import AgentInputEnvelope, AgentOutputEnvelope, AgentRunStatus

router = APIRouter(prefix="/automation", tags=["automation"])

_LABEL = "Generated and reviewed — not executed"


async def _queue_execution(session: AsyncSession, project: Project, generation_id: uuid.UUID):
    """Start the execution agent for a suite that was just written."""
    from apps.api.routers.v1.executions import start_execution_run

    try:
        return await start_execution_run(session, project, generation_id, run_destructive=False)
    except HTTPException as exc:
        if exc.status_code in {409, 503}:
            return None
        raise


def guard_eligible(
    project_id: uuid.UUID,
    requested_ids: list[uuid.UUID] | None,
    cases: list[CaseSnapshot],
    requirements: dict[uuid.UUID, RequirementSnapshot],
    maps: dict[uuid.UUID, MapSnapshot],
) -> list[CaseSnapshot]:
    try:
        return select_eligible(project_id, requested_ids, cases, requirements, maps)
    except AutomationEligibilityError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc


def artifact_root(settings: ApiSettings | None = None) -> Path:
    configured = Path((settings or ApiSettings()).automation_artifact_dir)
    if not configured.is_absolute():
        configured = Path(__file__).resolve().parents[4] / configured
    configured.mkdir(parents=True, exist_ok=True)
    return configured.resolve()


def presentation_status(scripts: list[AutomationScript]) -> str:
    statuses = {script.review_status for script in scripts}
    if AutomationReviewStatus.PENDING_APPROVAL in statuses:
        return AutomationReviewStatus.PENDING_APPROVAL
    if AutomationReviewStatus.REJECTED in statuses:
        return AutomationReviewStatus.REJECTED
    if AutomationReviewStatus.APPROVED in statuses:
        return AutomationReviewStatus.APPROVED
    if statuses == {AutomationReviewStatus.BLOCKED}:
        return AutomationReviewStatus.BLOCKED
    return AutomationReviewStatus.REVIEWED


@router.post("/projects/{project_id}/generate", response_model=AutomationGenerationOut)
async def generate_automation(
    project_id: uuid.UUID,
    body: GenerateAutomationRequest,
    session: AsyncSession = Depends(get_db_session),
) -> AutomationGenerationOut:
    settings = ApiSettings()
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    pairs = await TestCaseRepository(session).list_for_project(project_id)
    requirements = await _requirements(session, pairs)
    maps = await _maps(session, pairs)
    eligible = guard_eligible(
        project_id,
        body.test_case_ids,
        [_snapshot(case) for case, _version in pairs],
        requirements,
        maps,
    )
    eligible_ids = {case.id for case in eligible}
    selected = [(case, version) for case, version in pairs if case.id in eligible_ids]
    app_map = maps[selected[0][0].application_map_id]
    full_map = await ApplicationMapRepository(session).get_with_states(app_map.id)
    if full_map is None:
        raise HTTPException(status_code=409, detail="The matching application map is not available.")

    generation_id = uuid.uuid4()
    suite_dir = generation_dir(artifact_root(settings), project_id, generation_id)
    states = {
        state.state_code: {
            "url_pattern": state.url_pattern,
            "elements": state.elements,
        }
        for state in full_map.states
    }
    case_payload = [_case_payload(case, version, full_map.version) for case, version in selected]
    forbidden = _forbidden(case_payload)
    gen_request = AgentInputEnvelope(
        agent_run_id=uuid.uuid4(),
        project_id=project_id,
        trigger="manual",
        payload={
            "generation_id": str(generation_id),
            "application_url": project.application_url,
            "cases": case_payload,
            "states": states,
        },
        constraints={"suite_dir": str(suite_dir)},
    )
    try:
        plan = await AutomationGenerationAgent().run(gen_request)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _record_run(session, gen_request, AutomationGenerationAgent.name, AgentRunStatus.SUCCESS)

    review_request = AgentInputEnvelope(
        agent_run_id=uuid.uuid4(),
        project_id=project_id,
        trigger="manual",
        payload={"forbidden_literals": sorted(forbidden)},
        constraints={"suite_dir": str(suite_dir), "run_node": True},
    )
    review = await AutomationReviewAgent().run(review_request)
    _record_run(
        session,
        review_request,
        AutomationReviewAgent.name,
        AgentRunStatus.SUCCESS if review.verification.status != "FAILED" else AgentRunStatus.FAILED,
    )

    environment = settings.environment
    summary = {
        "selector_summary": plan.selector_summary,
        "lint": review.lint.as_dict(),
        "verification": review.verification.as_dict(),
        "risk_level": plan.risk_level,
        "file_tree": list_files(suite_dir),
        "application_map_id": str(full_map.id),
        "application_map_version": full_map.version,
    }
    repo = AutomationRepository(session)
    rows: list[AutomationScript] = []
    approvals: list[Approval] = []
    for script in plan.scripts:
        needs_approval = requires_approval("automation_script", script.risk, environment)
        if needs_approval:
            status = AutomationReviewStatus.PENDING_APPROVAL
        elif script.blocked:
            status = AutomationReviewStatus.BLOCKED
        else:
            status = AutomationReviewStatus.REVIEWED
        row = AutomationScript(
            id=uuid.uuid4(),
            script_code=await repo.next_script_code(project_id),
            project_id=project_id,
            generation_id=generation_id,
            test_case_id=script.test_case_id,
            test_case_code=script.tc_code,
            test_case_version=script.version,
            requirement_id=script.requirement_id,
            requirement_version=script.requirement_version,
            application_map_id=script.application_map_id,
            application_map_version=script.application_map_version,
            framework="playwright",
            file_path=script.spec_path,
            selector_strategy=script.selectors,
            risk_level=script.risk,
            review_status=status,
            review_findings={"blocked_reason": script.blocked_reason},
            suite_summary=summary,
            current_version=1,
            created_at=datetime.now(UTC),
        )
        repo.add_script(
            row,
            {
                "file_path": row.file_path,
                "selector_strategy": row.selector_strategy,
                "risk_level": row.risk_level,
                "review_status": row.review_status,
                "review_findings": row.review_findings,
                "suite_summary": summary,
            },
        )
        rows.append(row)
        if needs_approval:
            approval = Approval(
                id=uuid.uuid4(),
                project_id=project_id,
                target_type="automation_script",
                target_id=row.id,
                target_version=row.current_version,
                status=ApprovalStatus.PENDING,
            )
            session.add(approval)
            approvals.append(approval)
    response = _detail(
        rows,
        summary,
        [item.id for item in approvals],
        settings,
        include_sources=True,
    )
    await session.commit()
    queued = await _queue_execution(session, project, generation_id)
    if queued is not None:
        response.execution_job_id = uuid.UUID(queued.job_id)
        response.execution_run_id = queued.run_id
        response.label = "Generated — Playwright is running on the server"
    return response


@router.get("/projects/{project_id}", response_model=AutomationListOut)
async def list_generations(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AutomationListOut:
    scripts = await AutomationRepository(session).list_for_project(project_id)
    grouped: dict[uuid.UUID, list[AutomationScript]] = {}
    for script in scripts:
        grouped.setdefault(script.generation_id, []).append(script)
    settings = ApiSettings()
    execution_repo = ExecutionRepository(session)
    generations = [
        AutomationGenerationSummary(
            generation_id=generation_id,
            created_at=min(item.created_at for item in items),
            risk_level=_max_risk(item.risk_level for item in items),
            review_status=presentation_status(items),
            verification_status=str((items[0].suite_summary or {}).get("verification", {}).get("status") or "NOT_VERIFIED"),
            script_count=len(items),
            blocked_count=sum(1 for item in items if item.review_status == AutomationReviewStatus.BLOCKED or (item.review_findings or {}).get("blocked_reason")),
            approval_required=any(
                requires_approval("automation_script", item.risk_level, settings.environment) for item in items
            ),
            executed=bool((items[0].suite_summary or {}).get("executed"))
            or await execution_repo.has_completed_run(project_id, generation_id),
        )
        for generation_id, items in grouped.items()
    ]
    generations.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return AutomationListOut(generations=generations)


@router.get(
    "/projects/{project_id}/generations/{generation_id}",
    response_model=AutomationGenerationOut,
)
async def get_generation(
    project_id: uuid.UUID,
    generation_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> AutomationGenerationOut:
    rows = await _require_generation(session, project_id, generation_id)
    approvals = await _approval_ids(session, [row.id for row in rows])
    summary = dict(rows[0].suite_summary or {})
    summary["file_tree"] = _files_or_summary(project_id, generation_id, summary)
    executed = bool(summary.get("executed")) or await ExecutionRepository(session).has_completed_run(
        project_id, generation_id
    )
    return _detail(rows, summary, approvals, ApiSettings(), include_sources=True, executed=executed)


@router.get("/projects/{project_id}/generations/{generation_id}/download")
async def download_generation(
    project_id: uuid.UUID,
    generation_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    await _require_generation(session, project_id, generation_id)
    root = artifact_root()
    suite = generation_dir(root, project_id, generation_id)
    if not suite.is_dir():
        raise HTTPException(status_code=404, detail="Generated suite files are not available.")
    payload = build_zip(root, suite)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="playwright-{generation_id}.zip"'},
    )


def _detail(
    rows: list[AutomationScript],
    summary: dict,
    approval_ids: list[uuid.UUID],
    settings: ApiSettings,
    *,
    include_sources: bool,
    executed: bool = False,
) -> AutomationGenerationOut:
    generation_id = rows[0].generation_id
    project_id = rows[0].project_id
    links = ide_links(settings.automation_host_root, project_id, generation_id)
    suite = generation_dir(artifact_root(settings), project_id, generation_id)
    sources = read_sources(suite) if include_sources and suite.is_dir() else []
    blocked = [
        BlockedCaseOut(
            test_case_id=row.test_case_id,
            test_case_code=row.test_case_code,
            test_case_version=row.test_case_version,
            reason=str((row.review_findings or {}).get("blocked_reason") or "Blocked."),
        )
        for row in rows
        if (row.review_findings or {}).get("blocked_reason")
    ]
    verification = summary.get("verification") or {"status": "NOT_VERIFIED"}
    lint = summary.get("lint") or {}
    return AutomationGenerationOut(
        generation_id=generation_id,
        created_at=min(row.created_at for row in rows),
        scripts=[_script_out(row) for row in rows],
        blocked=blocked,
        file_tree=list(summary.get("file_tree") or []),
        selector_summary=dict(summary.get("selector_summary") or {}),
        lint=LintOut(**{key: lint.get(key, 0 if key != "findings" else []) for key in LintOut.model_fields}),
        risk_level=_max_risk(row.risk_level for row in rows),
        review_status=presentation_status(rows),
        approval_required=any(
            requires_approval("automation_script", row.risk_level, settings.environment) for row in rows
        ),
        approval_ids=approval_ids,
        verification=VerificationOut(
            status=str(verification.get("status") or "NOT_VERIFIED"),
            tsc=str(verification.get("tsc") or "NOT_VERIFIED"),
            playwright_list=str(verification.get("playwright_list") or "NOT_VERIFIED"),
            detail=str(verification.get("detail") or ""),
        ),
        download_url=f"/api/v1/automation/projects/{project_id}/generations/{generation_id}/download",
        vscode_url=None if links is None else links["vscode"],
        cursor_url=None if links is None else links["cursor"],
        executed=executed,
        label="Executed against the application" if executed else _LABEL,
        sources=[SourceFileOut(**item) for item in sources],
    )


def _script_out(row: AutomationScript) -> ScriptOut:
    return ScriptOut(
        script_id=row.id,
        script_code=row.script_code,
        test_case_id=row.test_case_id,
        test_case_code=row.test_case_code,
        test_case_version=row.test_case_version,
        requirement_id=row.requirement_id,
        requirement_version=row.requirement_version,
        application_map_id=row.application_map_id,
        application_map_version=row.application_map_version,
        framework=row.framework,
        file_path=row.file_path,
        risk_level=row.risk_level,
        review_status=row.review_status,
        blocked_reason=(row.review_findings or {}).get("blocked_reason"),
        selector_strategy=list(row.selector_strategy or []),
    )


async def _require_generation(
    session: AsyncSession, project_id: uuid.UUID, generation_id: uuid.UUID
) -> list[AutomationScript]:
    rows = await AutomationRepository(session).list_generation(project_id, generation_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Automation generation not found")
    return rows


async def _approval_ids(session: AsyncSession, script_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if not script_ids:
        return []
    result = await session.execute(
        select(Approval.id).where(
            Approval.target_type == "automation_script",
            Approval.target_id.in_(script_ids),
        )
    )
    return list(result.scalars().all())


async def _requirements(
    session: AsyncSession, pairs: list[tuple[TestCase, TestCaseVersion]]
) -> dict[uuid.UUID, RequirementSnapshot]:
    found: dict[uuid.UUID, RequirementSnapshot] = {}
    for case, _version in pairs:
        if case.requirement_id in found:
            continue
        requirement = await session.get(Requirement, case.requirement_id)
        if requirement is not None:
            found[requirement.id] = RequirementSnapshot(
                id=requirement.id,
                project_id=requirement.project_id,
                status=requirement.status,
                current_version=requirement.current_version,
            )
    return found


async def _maps(
    session: AsyncSession, pairs: list[tuple[TestCase, TestCaseVersion]]
) -> dict[uuid.UUID, MapSnapshot]:
    found: dict[uuid.UUID, MapSnapshot] = {}
    for case, _version in pairs:
        if case.application_map_id in found:
            continue
        app_map = await session.get(ApplicationMap, case.application_map_id)
        if app_map is not None:
            found[app_map.id] = MapSnapshot(
                id=app_map.id,
                project_id=app_map.project_id,
                version=app_map.version,
            )
    return found


def _snapshot(case: TestCase) -> CaseSnapshot:
    return CaseSnapshot(
        id=case.id,
        tc_code=case.tc_code,
        project_id=case.project_id,
        status=case.status,
        requirement_id=case.requirement_id,
        requirement_version=case.requirement_version,
        application_map_id=case.application_map_id,
    )


def _case_payload(case: TestCase, version: TestCaseVersion, map_version: int) -> dict:
    return {
        "test_case_id": str(case.id),
        "tc_code": case.tc_code,
        "version": version.version,
        "title": version.title,
        "requirement_id": str(case.requirement_id),
        "requirement_version": case.requirement_version,
        "application_map_id": str(case.application_map_id),
        "application_map_version": map_version,
        "project_id": str(case.project_id),
        "steps": version.steps,
        "test_data": version.test_data,
        "expected_result": version.expected_result,
        "credential_ref": case.credential_ref,
    }


def _forbidden(cases: list[dict]) -> set[str]:
    parsed = [
        CaseInput(
            test_case_id=uuid.UUID(item["test_case_id"]),
            tc_code=item["tc_code"],
            version=item["version"],
            title=item["title"],
            requirement_id=uuid.UUID(item["requirement_id"]),
            requirement_version=item["requirement_version"],
            application_map_id=uuid.UUID(item["application_map_id"]),
            application_map_version=item["application_map_version"],
            project_id=uuid.UUID(item["project_id"]),
            steps=item["steps"],
            test_data=item["test_data"],
            expected_result=item["expected_result"],
            credential_ref=item.get("credential_ref"),
        )
        for item in cases
    ]
    return _forbidden_literals(parsed)


def _files_or_summary(project_id: uuid.UUID, generation_id: uuid.UUID, summary: dict) -> list[str]:
    suite = generation_dir(artifact_root(), project_id, generation_id)
    if suite.is_dir():
        return list_files(suite)
    return list(summary.get("file_tree") or [])


def _max_risk(levels) -> str:
    rank = {RiskLevel.SAFE: 0, RiskLevel.REVIEW: 1, RiskLevel.DESTRUCTIVE: 2}
    return max(list(levels) or [RiskLevel.SAFE], key=lambda item: rank.get(RiskLevel(item), 0))


def _record_run(
    session: AsyncSession,
    request: AgentInputEnvelope,
    agent_name: str,
    status: AgentRunStatus,
) -> None:
    payload = request.model_dump(mode="json")
    for case in payload.get("payload", {}).get("cases", []):
        case.pop("credential_ref", None)
    payload.get("payload", {}).pop("forbidden_literals", None)
    output = AgentOutputEnvelope(agent_run_id=request.agent_run_id, status=status)
    session.add(
        AgentRun(
            id=request.agent_run_id,
            project_id=request.project_id,
            agent_name=agent_name,
            status=status,
            input_envelope=payload,
            output_envelope=output.model_dump(mode="json"),
            finished_at=datetime.now(UTC),
        )
    )
