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
