import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.agents.test_design.agent import TestDesignAgent as DesignAgent
from schemas.envelope import AgentInputEnvelope


@pytest.mark.asyncio
async def test_partial_map_without_supported_candidates_returns_deferred_result(monkeypatch):
    monkeypatch.setenv("TEST_DESIGN_USE_LLM", "false")
    project = uuid.uuid4()
    requirement = SimpleNamespace(project_id=project, status="APPROVED", req_code="REQ-1", current_version=1)
    version = SimpleNamespace(title="Place an order", description="Ordering", acceptance_criteria=[
        {"id": "AC-1", "text": "An order can be placed"},
    ])
    state = SimpleNamespace(state_code="STATE-001", fingerprint="login", url_pattern="/login", reached_via=[], elements=[])
    app_map = SimpleNamespace(id=uuid.uuid4(), project_id=project, status="PARTIAL", version=1,
        states=[state], coverage={}, discovery_checkpoint={})
    maps = SimpleNamespace(get_latest_for_project=AsyncMock(return_value=app_map), mark_generated_fingerprints=AsyncMock())
    cases = SimpleNamespace(existing_signatures=AsyncMock(return_value=set()),
        list_for_requirement=AsyncMock(return_value=[]), session=SimpleNamespace(commit=AsyncMock()))
    agent = DesignAgent(SimpleNamespace(get_with_current_version=AsyncMock(return_value=(requirement, version))),
        maps, cases, llm_client=object())
    result = await agent.run(AgentInputEnvelope(agent_run_id=uuid.uuid4(), project_id=project,
        trigger="manual", payload={"requirement_id": str(uuid.uuid4())}))
    assert result.outcome == "no_supported_cases"
    assert result.test_cases == []
    assert result.ac_evidence[0]["status"] == "deferred"
    assert result.envelope.status == "PARTIAL"
    assert result.envelope.artifacts == []
    maps.mark_generated_fingerprints.assert_not_awaited()


@pytest.mark.asyncio
async def test_generated_case_enriches_matching_import_without_second_row():
    from core.agents.test_design.schemas import TestCaseSpec

    existing = SimpleNamespace(id=uuid.uuid4(), current_version=1, status="DRAFT", source="excel_import")
    version = SimpleNamespace(id=uuid.uuid4(), title="Enter a valid username", expected_result="Username accepts input",
                              traceability=[], steps=[{"action": "navigate", "target": {"state_code": "S1"}}])
    async def append(case, data):
        case.current_version += 1
    repo = SimpleNamespace(find_semantic_duplicate=AsyncMock(return_value=(existing, version)),
        append_version=AsyncMock(side_effect=append), create=AsyncMock(), session=SimpleNamespace(commit=AsyncMock()))
    agent = DesignAgent(SimpleNamespace(), SimpleNamespace(), repo, llm_client=object())
    spec = TestCaseSpec(title="Verify username accepts valid input", objective="Verify valid input", category="POSITIVE",
        preconditions=["Login page is open"], steps=[
            {"step_number": 1, "action": "navigate", "target": {"state_code": "S1"}},
            {"step_number": 2, "action": "fill", "target": {"state_code": "S1", "element_code": "U"}, "value": "{username}"},
            {"step_number": 3, "action": "assert", "target": {"state_code": "S1", "element_code": "U"}, "expected": "Username accepts input"}],
        expected_result="Username accepts input", test_data={"username": "VALID_TEST_FIXTURE:username"}, traceability=["AC-1"], confidence=0.7)
    artifacts, skipped, merged, _ = await agent._persist_specs(project_id=uuid.uuid4(), requirement_id=uuid.uuid4(),
        requirement_version=1, application_map_id=uuid.uuid4(), specs=[spec], known_signatures=set())
    repo.create.assert_not_awaited()
    assert repo.append_version.call_args.args[1]["title"] == version.title
    assert repo.append_version.call_args.args[1]["traceability"] == ["AC-1"]
    assert artifacts[0].id == str(existing.id)
    assert artifacts[0].version == 2
    assert merged == 1 and skipped == 0
