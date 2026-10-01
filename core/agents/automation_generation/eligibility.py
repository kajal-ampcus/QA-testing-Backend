"""Which approved test cases may enter automation generation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from domain.enums import RequirementStatus, TestCaseStatus


class AutomationEligibilityError(Exception):
    """The request cannot generate a suite. The API maps this to HTTP 409."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class CaseSnapshot:
    id: UUID
    tc_code: str
    project_id: UUID
    status: str
    requirement_id: UUID
    requirement_version: int
    application_map_id: UUID


@dataclass
class RequirementSnapshot:
    id: UUID
    project_id: UUID
    status: str
    current_version: int


@dataclass
class MapSnapshot:
    id: UUID
    project_id: UUID
    version: int


def select_eligible(
    project_id: UUID,
    requested_ids: list[UUID] | None,
    cases: list[CaseSnapshot],
    requirements: dict[UUID, RequirementSnapshot],
    maps: dict[UUID, MapSnapshot],
) -> list[CaseSnapshot]:
    """Return the cases to generate, or raise when none are legally selectable."""
    by_id = {case.id: case for case in cases}
    if requested_ids is not None:
        if not requested_ids:
            raise AutomationEligibilityError(
                "No test cases were selected. Select at least one APPROVED test case."
            )
        chosen: list[CaseSnapshot] = []
        for case_id in requested_ids:
            case = by_id.get(case_id)
            if case is None or case.project_id != project_id:
                raise AutomationEligibilityError(
                    f"Test case {case_id} does not belong to this project."
                )
            problem = _ineligible_reason(case, requirements, maps, project_id)
            if problem:
                raise AutomationEligibilityError(problem)
            chosen.append(case)
        _require_one_map(chosen, maps)
        return chosen

    eligible = [
        case
        for case in cases
        if case.project_id == project_id and not _ineligible_reason(case, requirements, maps, project_id)
    ]
    if not eligible:
        raise AutomationEligibilityError(
            "No approved test cases are available to generate automation. "
            "Approve a current test case before generating a Playwright suite."
        )
    _require_one_map(eligible, maps)
    return eligible


def _require_one_map(cases: list[CaseSnapshot], maps: dict[UUID, MapSnapshot]) -> None:
    map_ids = {case.application_map_id for case in cases}
    if len(map_ids) > 1:
        detail = ", ".join(
            f"{case.tc_code} uses {case.application_map_id}" for case in cases
        )
        raise AutomationEligibilityError(
            "Selected test cases must share one application map. " + detail
        )
    only = maps.get(next(iter(map_ids)))
    if only is None:
        raise AutomationEligibilityError(
            "The selected test cases reference an application map that is not available."
        )


def _ineligible_reason(
    case: CaseSnapshot,
    requirements: dict[UUID, RequirementSnapshot],
    maps: dict[UUID, MapSnapshot],
    project_id: UUID,
) -> str | None:
    if case.project_id != project_id:
        return f"{case.tc_code} does not belong to this project."
    requirement = requirements.get(case.requirement_id)
    app_map = maps.get(case.application_map_id)
    if case.status == TestCaseStatus.OUTDATED:
        return (
            f"{case.tc_code} is outdated and cannot be automated. "
            "Revise it and approve the new version."
        )
    if case.status != TestCaseStatus.APPROVED:
        return (
            f"{case.tc_code} is {case.status} and cannot be automated. "
            "Only APPROVED test cases can generate a Playwright suite."
        )
    if requirement is None or requirement.project_id != project_id:
        return f"{case.tc_code} is not linked to a requirement in this project."
    if requirement.status != RequirementStatus.APPROVED:
        return f"{case.tc_code} is stale because its requirement is {requirement.status}."
    if requirement.current_version != case.requirement_version:
        return (
            f"{case.tc_code} is stale: it was approved for requirement version "
            f"{case.requirement_version}, and the current version is {requirement.current_version}."
        )
    if app_map is None or app_map.project_id != project_id:
        return f"{case.tc_code} references an application map that is not available."
    return None
