from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from core.agents.test_design.discovery_requirements import discovery_draft, matching_requirement
from apps.api.routers.v1 import requirements as api


def state(path="/en/feedback"):
    return NS(state_code="STATE-004", url_pattern=path, elements=[
        {"role": "link", "name": "Login", "landmark": "banner"},
        {"role": "heading", "name": "Feedback"},
        {"role": "textbox", "name": "Name *", "landmark": "form"},
        {"role": "textbox", "name": "Search", "landmark": "banner"},
        {"role": "button", "name": "Submit Feedback", "input_type": "submit", "landmark": "form"}])


def version(title="Login", tags=None):
    return NS(title=title, description="Click Login to open the login form", domain_tags=tags or [],
              acceptance_criteria=[{"id": "AC-1", "text": "Login accepts valid credentials", "source": "REQUIREMENT"}])


def test_global_login_link_is_not_feedback_requirement_coverage():
    assert matching_requirement(state(), version()) is None
    assert matching_requirement(state("/en/login"), version()) == "suggested"
    assert matching_requirement(state("/en"), version()) == "suggested"
    assert matching_requirement(state(), version("Feedback submission")) == "suggested"


def test_explicit_links_override_name_suggestions_and_remain_scoped():
    linked = version(tags=["discovery-route:/en/feedback"])
    assert matching_requirement(state(), linked) == "linked"
    assert matching_requirement(state("/en/login"), linked) is None


@pytest.mark.parametrize("exploratory", [True, False])
def test_draft_uses_local_controls_without_inventing_business_outcomes(exploratory):
    draft = discovery_draft([state()], exploratory)
    ac = next(ac for ac in draft["acceptance_criteria"] if "Name *" in ac["text"])
    assert "Name *" in ac["text"]
    assert "Search" not in ac["text"]
    assert "Login" not in ac["text"]
    assert "success" not in ac["text"]
    assert ac["source"] == "OBSERVED_DOM"
    assert draft["domain_tags"][0] == "discovery-route:/en/feedback"
    assert ("exploratory" in draft["domain_tags"]) == exploratory


def test_discovery_draft_generates_real_visibility_tests_for_message_and_submit_without_submitting():
    from core.agents.test_design.scenarios import plan_scenarios
    page = state()
    page.elements += [{"role": "textbox", "name": "Message *", "landmark": "form"}]
    for index, element in enumerate(page.elements):
        element.update(element_code=f"EL-{index}", source="OBSERVED_DOM")
    draft = discovery_draft([page])
    plan = plan_scenarios(draft["acceptance_criteria"], [vars(page)], draft["title"], draft["description"],
                          starting_states={page.state_code})
    assert len(plan.cases) == 1
    assert any(step.target.element_name == "Message *" for case in plan.cases for step in case.steps)
    assert any(step.target.element_name == "Submit Feedback" for case in plan.cases for step in case.steps)
    assert all(step.action in {"navigate", "assert"} for case in plan.cases for step in case.steps)
    from core.agents.test_case_validation.agent import TestCaseValidationAgent
    validator = TestCaseValidationAgent()
    for case in plan.cases:
        assert not validator.validate(case, [vars(page)], {ac["id"] for ac in draft["acceptance_criteria"]},
            strict=True, assertion_sources=[ac["text"] for ac in draft["acceptance_criteria"] if ac["id"] in case.traceability]).issues
    negative = plan_scenarios(draft["acceptance_criteria"], [vars(page)], draft["title"], draft["description"],
                             requested_categories={ac["id"]: {"NEGATIVE"} for ac in draft["acceptance_criteria"]})
    assert not negative.cases


def test_nested_header_search_and_floating_chatbot_are_not_form_requirements():
    page = state()
    page.elements += [
        {"role": "textbox", "name": "Search", "landmark": "form", "xpath": "/html/body/header/form/input"},
        {"role": "button", "name": "Open chatbot"},
    ]
    draft = discovery_draft([page])
    text = " ".join(ac["text"] for ac in draft["acceptance_criteria"])
    assert "Name *" in text
    assert "Search" not in text
    assert "chatbot" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign,unknown", [(True, False), (False, True)])
async def test_draft_rejects_other_project_or_unknown_state(monkeypatch, foreign, unknown):
    project_id, map_id = uuid4(), uuid4()
    app_map = NS(project_id=uuid4() if foreign else project_id, states=[state()])
    monkeypatch.setattr("infra.db.repositories.application_map_repo.ApplicationMapRepository.get_with_states",
                        AsyncMock(return_value=app_map))
    body = api.DiscoveryRequirementDraft(application_map_id=map_id, state_codes=["missing" if unknown else "STATE-004"])
    with pytest.raises(HTTPException) as error:
        await api.draft_discovery_requirement(project_id, body, NS())
    assert error.value.status_code == (404 if foreign else 422)


@pytest.mark.asyncio
async def test_preview_never_creates_or_approves_requirement(monkeypatch):
    monkeypatch.setattr(api, "_discovery_states", AsyncMock(return_value=[state()]))
    session = NS(add=AsyncMock(), commit=AsyncMock())
    result = await api.draft_discovery_requirement(uuid4(), api.DiscoveryRequirementDraft(
        application_map_id=uuid4(), state_codes=["STATE-004"]), session)
    assert "draft" in result
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_saved_draft_uses_normal_pending_approval_without_editing_existing_requirement(monkeypatch):
    monkeypatch.setattr(api, "_discovery_states", AsyncMock(return_value=[state()]))
    repo = NS(next_req_code=AsyncMock(return_value="REQ-002"), create=AsyncMock())
    monkeypatch.setattr(api, "RequirementRepository", lambda session: repo)
    requirement = NS(id=uuid4(), project_id=uuid4(), current_version=1, status="PENDING_APPROVAL")
    repo.create.return_value = (requirement, NS())
    monkeypatch.setattr(api, "_to_response", lambda req, ver: {"status": req.status})
    approvals = []
    session = NS(scalar=AsyncMock(return_value=None), add=approvals.append, commit=AsyncMock())
    result = await api.draft_discovery_requirement(requirement.project_id, api.DiscoveryRequirementDraft(
        application_map_id=uuid4(), state_codes=["STATE-004"], save=True, criteria=["Name is visible"]), session)
    assert result["requirement"]["status"] == "PENDING_APPROVAL"
    assert approvals[0].target_id == requirement.id
    assert approvals[0].target_version == 1
    assert repo.create.call_args.args[4] == "PENDING_APPROVAL"


@pytest.mark.asyncio
async def test_link_rejects_stale_requirement_version(monkeypatch):
    monkeypatch.setattr(api, "_discovery_states", AsyncMock(return_value=[state()]))
    project_id = uuid4()
    monkeypatch.setattr(api, "_lock_requirement", AsyncMock(return_value=NS(project_id=project_id, current_version=2)))
    with pytest.raises(HTTPException) as error:
        await api.link_discovery_requirement(project_id, uuid4(), api.DiscoveryRequirementLink(
            application_map_id=uuid4(), state_codes=["STATE-004"], expected_version=1), NS())
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_link_copies_ac_into_revision_and_uses_existing_approval_lifecycle(monkeypatch):
    monkeypatch.setattr(api, "_discovery_states", AsyncMock(return_value=[state()]))
    project_id = uuid4()
    requirement = NS(id=uuid4(), project_id=project_id, current_version=1)
    current = version()
    current.ambiguities, current.raw_text = [], "Original login requirements"
    monkeypatch.setattr(api, "_lock_requirement", AsyncMock(return_value=requirement))
    monkeypatch.setattr(api, "_current_version", AsyncMock(return_value=current))
    repo = NS(append_version=AsyncMock(return_value=NS()))
    monkeypatch.setattr(api, "RequirementRepository", lambda session: repo)
    lifecycle = AsyncMock()
    monkeypatch.setattr(api, "_after_new_version", lifecycle)
    monkeypatch.setattr(api, "_to_response", lambda req, ver: "review revision")
    session = NS(commit=AsyncMock())
    result = await api.link_discovery_requirement(project_id, requirement.id, api.DiscoveryRequirementLink(
        application_map_id=uuid4(), state_codes=["STATE-004"], expected_version=1), session)
    extraction = repo.append_version.call_args.args[2]
    assert extraction.acceptance_criteria[0].text == current.acceptance_criteria[0]["text"]
    assert extraction.domain_tags == ["discovery-route:/en/feedback"]
    lifecycle.assert_awaited_once()
    assert result == "review revision"


@pytest.mark.asyncio
async def test_repeat_draft_returns_existing_requirement_without_duplicate_creation(monkeypatch):
    monkeypatch.setattr(api, "_discovery_states", AsyncMock(return_value=[state()]))
    repo = NS(next_req_code=AsyncMock(return_value="REQ-003"), create=AsyncMock())
    monkeypatch.setattr(api, "RequirementRepository", lambda session: repo)
    existing = NS(id=uuid4())
    monkeypatch.setattr(api, "_current_version", AsyncMock(return_value=NS()))
    monkeypatch.setattr(api, "_to_response", lambda req, ver: {"id": str(req.id)})
    session = NS(scalar=AsyncMock(return_value=existing), add=AsyncMock(), commit=AsyncMock())
    result = await api.draft_discovery_requirement(uuid4(), api.DiscoveryRequirementDraft(
        application_map_id=uuid4(), state_codes=["STATE-004"], save=True), session)
    assert result["requirement"]["id"] == str(existing.id)
    repo.create.assert_not_called()
    session.add.assert_not_called()
