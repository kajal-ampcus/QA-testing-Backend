from uuid import uuid4

import pytest

from core.agents.automation_generation.execution_plan import ground_steps
from core.agents.automation_generation.recovery import recover_cases
from core.agents.automation_generation.selector_strategy import match_observed_element
from core.agents.automation_generation.suite import CaseInput, StateInput
from core.agents.input_resolution.coordinator import resolve_input


def state(elements=None):
    return StateInput("S1", "/form", elements or [
        {"element_code": "E1", "role": "textbox", "name": "Username"},
        {"element_code": "E2", "role": "textbox", "name": "Password"},
    ])


def case(code="TC-1"):
    return CaseInput(uuid4(), code, 1, "Check Username", uuid4(), 1, uuid4(), 1, uuid4(),
                    [{"action": "fill", "target": {"state_code": "S1", "element_name": "Username"}, "value": "fixture"},
                     {"action": "assert", "target": {"state_code": "S1", "element_name": "Username"},
                      "expected": "Username", "assertion": {"kind": "visible"}}], {}, "Username")


def test_named_negative_field_does_not_choose_first_control():
    steps, issues = ground_steps([
        {"action": "assert", "target": {"state_code": "S1"}, "expected": "Leave Password empty"}
    ], {"S1": state()}, {})
    assert not issues
    assert steps[0]["target"]["element_code"] == "E2"
    assert steps[0]["action"] == "fill"
    assert steps[0]["value"] == ""


def test_natural_language_action_and_observed_html_input_type_are_mapped():
    steps, issues = ground_steps([
        {"action": "Enter account email", "target": {"state_code": "S1", "input_type": "email"}}
    ], {"S1": state([
        {"element_code": "E1", "role": "textbox", "name": "Account email", "type": "email"},
    ])}, {"Account email": "person@example.test"})
    assert not issues
    assert steps[0]["action"] == "fill"
    assert steps[0]["target"]["element_code"] == "E1"
    assert steps[0]["value"] == "person@example.test"


def test_ambiguous_and_missing_matches_are_rejected():
    elements = state().elements
    elements.append({**elements[0], "element_code": "E3"})
    assert match_observed_element("S1", elements, {}, "fill", "Enter Username").status == "blocked"
    assert match_observed_element("S1", elements, {}, "fill", "Enter Address").status == "blocked"


def test_placeholder_match_uses_observed_selector():
    element = {"element_code": "E1", "role": "textbox", "placeholder": "Account name"}
    result = match_observed_element("S1", [element], {"placeholder": "Account name"}, "fill")
    assert result.status == "resolved"
    assert result.strategy == "getByPlaceholder"


async def test_recovery_retries_each_state_once_and_keeps_other_cases():
    missing = StateInput("S1", "/form", [])
    calls = []

    async def refresh(current, item):
        calls.append(current.state_code)
        return state().elements

    good, bad = case(), case("TC-2")
    bad.steps[0]["target"]["element_name"] = "Unobserved"
    states, diagnostics, evidence = await recover_cases([good, bad], {"S1": missing}, uuid4(), refresh)
    assert calls == ["S1"]
    assert diagnostics[str(good.test_case_id)]["status"] == "RECOVERED"
    assert diagnostics[str(bad.test_case_id)]["status"] == "BLOCKED"
    assert evidence["S1"] == states["S1"].elements
    assert missing.elements == []


async def test_rediscovery_failure_is_case_local():
    async def refresh(current, item):
        raise TimeoutError()
    item = case()
    _, diagnostics, evidence = await recover_cases([item], {"S1": StateInput("S1", "/form", [])}, uuid4(), refresh)
    assert diagnostics[str(item.test_case_id)]["status"] == "BLOCKED"
    assert not evidence


@pytest.mark.parametrize("field", ["Password", "OTP", "CAPTCHA", "Existing account"])
async def test_no_invented_sensitive_or_business_data(field):
    assert await resolve_input({"field": field}) is None


async def test_configured_and_intentionally_blank_values():
    assert await resolve_input({"configured_value": "fixture"}) == {"mode": "value", "value": "fixture"}
    assert await resolve_input({"allow_empty": True, "negative_target": True}) == {"mode": "empty", "value": ""}


async def test_targeted_discovery_checks_destination_and_preserves_codes(monkeypatch):
    from core.agents.application_discovery import recovery

    async def validate(url):
        return None
    monkeypatch.setattr(recovery, "validate_discovery_target", validate)

    class Browser:
        destination = "https://example.test/form"
        navigations = []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def navigate_page(self, url):
            self.navigations.append(url)
        async def take_snapshot(self):
            return {"elements": [
                {"role": "RootWebArea", "url": self.destination},
                {"role": "textbox", "name": "Username", "uid": "transient"},
            ]}
    browser = Browser()
    class Gateway:
        def chrome_devtools(self, agent, pattern, **kwargs):
            assert agent == "application_discovery"
            return browser
    result = await recovery.rediscover_state(
        project_id=uuid4(), base_url="https://example.test", state=state(), gateway=Gateway(),
    )
    assert result[0]["element_code"] == "E1"
    assert "uid" not in result[0]
    assert browser.navigations == ["https://example.test/form"]
    browser.destination = "https://example.test/login"
    with pytest.raises(ValueError, match="different"):
        await recovery.rediscover_state(
            project_id=uuid4(), base_url="https://example.test", state=state(), gateway=Gateway(),
        )


def test_required_companion_uses_configured_value_and_preserves_negative():
    from core.agents.automation_generation.execution_plan import prepare_execution
    current = state()
    current.elements[0]["required"] = True
    current.elements.append({"element_code": "B1", "role": "button", "name": "Login"})
    item = case()
    item.category = "NEGATIVE"
    item.title = "Password empty"
    item.test_data = {"username": "configured-user"}
    item.steps = [
        {"action": "navigate", "target": {"state_code": "S1"}},
        {"action": "fill", "target": {"state_code": "S1", "element_code": "E2"}, "value": ""},
        {"action": "assert", "target": {"state_code": "S1", "element_code": "E2"}, "expected": "required field"},
    ]
    plan = prepare_execution(item, {"S1": current})
    assert not plan.blocked_reason
    fills = {step["target"]["element_code"]: step["value"] for step in plan.steps if step["action"] == "fill"}
    assert fills == {"E1": "configured-user", "E2": ""}
