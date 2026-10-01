from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.api.routers.v1.automation import guard_eligible
from core.agents.automation_generation.eligibility import (
    CaseSnapshot,
    MapSnapshot,
    RequirementSnapshot,
)
from domain.enums import RequirementStatus
from domain.enums import TestCaseStatus as CaseStatus


def _case(status: str, **overrides: object) -> CaseSnapshot:
    project_id = overrides.pop("project_id", None)
    base = dict(
        id=uuid4(),
        tc_code="TC-001",
        project_id=project_id or uuid4(),
        status=status,
        requirement_id=uuid4(),
        requirement_version=1,
        application_map_id=uuid4(),
    )
    base.update(overrides)
    return CaseSnapshot(**base)  # type: ignore[arg-type]


def _requirement(case: CaseSnapshot, status: str = RequirementStatus.APPROVED) -> RequirementSnapshot:
    return RequirementSnapshot(
        id=case.requirement_id,
        project_id=case.project_id,
        status=status,
        current_version=case.requirement_version,
    )


def _map(case: CaseSnapshot) -> MapSnapshot:
    return MapSnapshot(id=case.application_map_id, project_id=case.project_id, version=3)


def test_unapproved_only_project_returns_409() -> None:
    case = _case(CaseStatus.DRAFT)
    with pytest.raises(HTTPException) as error:
        guard_eligible(case.project_id, None, [case], {case.requirement_id: _requirement(case)}, {case.application_map_id: _map(case)})
    assert error.value.status_code == 409
    assert "approved" in error.value.detail.lower()


def test_explicit_draft_is_rejected_even_when_another_case_is_approved() -> None:
    project_id = uuid4()
    draft = _case(CaseStatus.DRAFT, project_id=project_id, tc_code="TC-001")
    approved = _case(CaseStatus.APPROVED, project_id=project_id, tc_code="TC-002", application_map_id=draft.application_map_id, requirement_id=draft.requirement_id)
    requirements = {draft.requirement_id: _requirement(draft)}
    maps = {draft.application_map_id: _map(draft)}
    with pytest.raises(HTTPException) as error:
        guard_eligible(project_id, [draft.id], [draft, approved], requirements, maps)
    assert error.value.status_code == 409
    assert "DRAFT" in error.value.detail


@pytest.mark.parametrize("status", [CaseStatus.PENDING_APPROVAL, CaseStatus.REJECTED, CaseStatus.OUTDATED])
def test_non_approved_statuses_are_rejected(status: str) -> None:
    case = _case(status)
    with pytest.raises(HTTPException) as error:
        guard_eligible(case.project_id, [case.id], [case], {case.requirement_id: _requirement(case)}, {case.application_map_id: _map(case)})
    assert error.value.status_code == 409


def test_project_mismatch_returns_409() -> None:
    case = _case(CaseStatus.APPROVED)
    with pytest.raises(HTTPException) as error:
        guard_eligible(uuid4(), [case.id], [case], {case.requirement_id: _requirement(case)}, {case.application_map_id: _map(case)})
    assert error.value.status_code == 409
    assert "does not belong" in error.value.detail


def test_stale_requirement_version_returns_409() -> None:
    case = _case(CaseStatus.APPROVED)
    requirement = _requirement(case)
    requirement.current_version = case.requirement_version + 1
    with pytest.raises(HTTPException) as error:
        guard_eligible(case.project_id, [case.id], [case], {requirement.id: requirement}, {case.application_map_id: _map(case)})
    assert error.value.status_code == 409
    assert "stale" in error.value.detail.lower()


def test_approved_case_is_eligible() -> None:
    case = _case(CaseStatus.APPROVED)
    selected = guard_eligible(
        case.project_id,
        [case.id],
        [case],
        {case.requirement_id: _requirement(case)},
        {case.application_map_id: _map(case)},
    )
    assert selected == [case]
