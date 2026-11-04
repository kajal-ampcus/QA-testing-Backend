from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from core.agents.test_design.discovery_requirements import discovery_draft
from core.agents.test_design.form_contracts import form_profile
from core.agents.test_design.scenarios import plan_scenarios, measure_coverage
from core.agents.test_case_validation.agent import TestCaseValidationAgent
from infra.test_data_service import field_requirements
from apps.api.routers.v1 import requirements as api


def feedback():
    elements = [{"role": "textbox", "name": "Search", "landmark": "form", "xpath": "/html/body/header/form/input"}]
    elements += [{"role": "textbox", "name": name + " *", "landmark": "form", "input_type": "text", "required": False}
                 for name in ["Name", "Subject", "Message"]]
    elements += [{"role": "StaticText", "name": "Rating", "landmark": "form"},
                 {"role": "StaticText", "name": "*", "landmark": "form"}]
    elements += [{"role": "radio", "name": f"{name} {name}", "landmark": "form", "checked": False,
                  "xpath": f"/html/body/main/form/div/label[{i+1}]/input"}
                 for i, name in enumerate(["Excellent", "Good", "Average", "Fair", "Needs Improvement"])]
    elements += [{"role": "button", "name": "Submit Feedback", "landmark": "form", "input_type": "submit"}]
    for i, e in enumerate(elements):
        e.update(element_code=f"EL-{i+1:03d}", source="OBSERVED_DOM", visible=True)
    return NS(state_code="STATE-004", url_pattern="/en/feedback", elements=elements)


def plan(page=None):
    page = page or feedback()
    draft = discovery_draft([page], behavioral=True, success_outcome="The reviewer-confirmed success message is displayed.")
    return page, draft, plan_scenarios(draft["acceptance_criteria"], [vars(page)], draft["title"], draft["description"])


def test_feedback_has_isolated_positives_negatives_and_one_smoke_all_strictly_validated():
    page, draft, result = plan()
    assert len(result.cases) == 15
    assert sum(case.category == "NEGATIVE" for case in result.cases) == 5
    assert sum(case.category == "POSITIVE" for case in result.cases) == 10
    assert len([case for case in result.cases if all(step.action in {"navigate", "assert"} for step in case.steps)]) == 1
    validator = TestCaseValidationAgent()
    for case in result.cases:
        sources = [ac["text"] for ac in draft["acceptance_criteria"] if ac["id"] in case.traceability]
        assert not validator.validate(case, [vars(page)], {ac["id"] for ac in draft["acceptance_criteria"]},
                                      strict=True, assertion_sources=sources).issues
        assert all(not (step.action == "fill" and step.target.element_role == "radio") for step in case.steps)
        assert "Search" not in [step.target.element_name for step in case.steps]
    assert measure_coverage(result, result.cases)["missing_scenarios"] == 0


def test_single_omission_keeps_other_values_and_rating_valid_and_prompts_only_for_needed_values():
    page, _, result = plan()
    case = next(case for case in result.cases if case.title == "Reject submission with Name blank")
    fills = [step for step in case.steps if step.action == "fill"]
    assert [step.target.element_name for step in fills if step.value == ""] == ["Name *"]
    assert len([step for step in case.steps if step.action == "click" and step.target.element_role == "radio"]) == 1
    rows = field_requirements([step.model_dump(mode="json") for step in case.steps], {page.state_code: page})
    assert {row["field_name"] for row in rows} == {"Name *", "Subject *", "Message *"}
    assert next(row for row in rows if row["field_name"] == "Name *")["explicit_value"] == ""
    assert all(value is None for value in case.test_data.values())


def test_missing_rating_does_not_reselect_it_or_request_a_value_for_it():
    page, _, result = plan()
    case = next(case for case in result.cases if case.title == "Reject submission without Rating")
    assert not any(step.action == "click" and step.target.element_role == "radio" for step in case.steps)
    rows = field_requirements([step.model_dump(mode="json") for step in case.steps], {page.state_code: page})
    assert len(rows) == 3
    assert not any(row["field_type"] == "radio" for row in rows)


def test_selection_titles_are_readable_but_targets_keep_observed_accessible_names():
    _, _, result = plan()
    case = next(case for case in result.cases if case.title == "Select Excellent in Rating")
    assert case.steps[1].target.element_name == "Excellent Excellent"
    assert case.steps[1].action == "click"
    assert case.steps[-1].assertion["kind"] == "manual"


def test_visibility_only_stays_visibility_only_and_login_contracts_are_untouched():
    page = feedback()
    draft = discovery_draft([page], exploratory=True)
    result = plan_scenarios(draft["acceptance_criteria"], [vars(page)], draft["title"], draft["description"])
    assert len(result.cases) == 1
    assert not any(case.category == "NEGATIVE" for case in result.cases)
    page.url_pattern = "/en/login"
    assert form_profile(page) is None


def test_changed_or_missing_contract_control_is_deferred_instead_of_guessed():
    page = feedback()
    draft = discovery_draft([page], behavioral=True)
    page.elements = [e for e in page.elements if e["name"] != "Name *"]
    result = plan_scenarios(draft["acceptance_criteria"], [vars(page)], draft["title"], draft["description"])
    assert any(s.spec is None for s in result.scenarios if "Name" in s.description)


@pytest.mark.asyncio
async def test_functional_draft_requires_confirmed_success_before_save(monkeypatch):
    monkeypatch.setattr(api, "_discovery_states", AsyncMock(return_value=[feedback()]))
    with pytest.raises(HTTPException) as error:
        await api.draft_discovery_requirement(uuid4(), api.DiscoveryRequirementDraft(
            application_map_id=uuid4(), state_codes=["STATE-004"], behavioral=True, save=True), NS())
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_functional_preview_exposes_proposals_and_does_not_modify_requirement(monkeypatch):
    monkeypatch.setattr(api, "_discovery_states", AsyncMock(return_value=[feedback()]))
    session = NS(add=AsyncMock(), commit=AsyncMock())
    result = await api.draft_discovery_requirement(uuid4(), api.DiscoveryRequirementDraft(
        application_map_id=uuid4(), state_codes=["STATE-004"], behavioral=True), session)
    assert result["requires_success_outcome"] is True
    assert any((ac.get("form_behavior") or {}).get("kind") == "missing_choice" for ac in result["draft"]["acceptance_criteria"])
    assert not any((ac.get("form_behavior") or {}).get("kind") == "submit" for ac in result["draft"]["acceptance_criteria"])
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_upgrading_approved_visibility_requirement_creates_reviewable_revision_with_contracts(monkeypatch):
    page = feedback()
    project_id, requirement_id = uuid4(), uuid4()
    requirement = NS(id=requirement_id, project_id=project_id, current_version=1, status="APPROVED")
    current = NS(title="Feedback", description="Feedback controls", domain_tags=["discovery-route:/en/feedback"],
                 acceptance_criteria=discovery_draft([page])["acceptance_criteria"])
    original = [dict(ac) for ac in current.acceptance_criteria]
    monkeypatch.setattr(api, "_discovery_states", AsyncMock(return_value=[page]))
    monkeypatch.setattr(api, "_lock_requirement", AsyncMock(return_value=requirement))
    monkeypatch.setattr(api, "_current_version", AsyncMock(return_value=current))
    saved_version = NS()
    repo = NS(append_version=AsyncMock(return_value=saved_version), create=AsyncMock())
    monkeypatch.setattr(api, "RequirementRepository", lambda session: repo)
    lifecycle = AsyncMock()
    monkeypatch.setattr(api, "_after_new_version", lifecycle)
    monkeypatch.setattr(api, "_to_response", lambda req, ver: {"id": str(req.id)})
    session = NS(commit=AsyncMock())
    await api.draft_discovery_requirement(project_id, api.DiscoveryRequirementDraft(
        application_map_id=uuid4(), state_codes=[page.state_code], save=True, behavioral=True,
        existing_requirement_id=requirement_id, expected_version=1,
        success_outcome="A confirmed success alert appears."), session)
    repo.append_version.assert_awaited_once()
    repo.create.assert_not_called()
    lifecycle.assert_awaited_once_with(session, requirement, saved_version)
    assert current.acceptance_criteria == original
    assert any((ac.get("form_behavior") or {}).get("kind") == "submit" for ac in saved_version.acceptance_criteria)
    assert any((ac.get("form_behavior") or {}).get("kind") == "missing" for ac in saved_version.acceptance_criteria)


@pytest.mark.asyncio
async def test_text_edit_does_not_reuse_observed_contract_for_different_rule(monkeypatch):
    monkeypatch.setattr(api, "_discovery_states", AsyncMock(return_value=[feedback()]))
    version = NS()
    req = NS(id=uuid4(), project_id=uuid4(), current_version=1, status="PENDING_APPROVAL")
    repo = NS(next_req_code=AsyncMock(return_value="REQ-003"), create=AsyncMock(return_value=(req, version)))
    monkeypatch.setattr(api, "RequirementRepository", lambda session: repo)
    monkeypatch.setattr(api, "_to_response", lambda req, ver: {"id": str(req.id)})
    session = NS(scalar=AsyncMock(return_value=None), add=lambda row: None, commit=AsyncMock())
    await api.draft_discovery_requirement(req.project_id, api.DiscoveryRequirementDraft(
        application_map_id=uuid4(), state_codes=["STATE-004"], behavioral=True, save=True,
        success_outcome="A confirmed success alert appears.", criteria=["Custom confirmed business rule."]), session)
    assert "form_behavior" not in version.acceptance_criteria[0]
    assert version.acceptance_criteria[0]["source"] == "REQUIREMENT"
    assert (version.acceptance_criteria[-1].get("form_behavior") or {}).get("kind") == "submit"
