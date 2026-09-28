"""Milestone 1 acceptance and negative-path checks against a test PostgreSQL DB."""

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is required"
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Select the isolated test DB before importing the application's engine.
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from apps.api import dependencies
    from apps.api.main import app
    from core.agents.requirement_understanding.agent import RequirementUnderstandingAgent
    from infra.llm.base import LLMStructuredResult

    test_engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    monkeypatch.setattr(
        dependencies, "AsyncSessionLocal", async_sessionmaker(test_engine, expire_on_commit=False)
    )

    class FakeLLM:
        async def call_structured(self, **kwargs: Any) -> LLMStructuredResult:
            ambiguous = "quickly" in kwargs["user_prompt"].lower()
            return LLMStructuredResult(
                data={
                    "title": "Test requirement",
                    "description": "Observed expected behavior",
                    "acceptance_criteria": [
                        {
                            "id": "AC-1",
                            "text": "The expected behavior occurs",
                            "source": "REQUIREMENT",
                        }
                    ],
                    "ambiguities": (
                        [{"field": "timing", "issue": "Quickly is not measurable"}]
                        if ambiguous
                        else []
                    ),
                    "domain_tags": ["test"],
                },
                model="test",
                input_tokens=1,
                output_tokens=1,
            )

    monkeypatch.setattr(
        RequirementUnderstandingAgent,
        "__init__",
        lambda self, llm_client=None: setattr(self, "llm_client", FakeLLM()),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_project_scoped_codes_and_approval(client: TestClient) -> None:
    project_one = client.post("/api/v1/projects", json={"name": "Project one"}).json()
    project_two = client.post("/api/v1/projects", json={"name": "Project two"}).json()
    for project in (project_one, project_two):
        response = client.post(
            "/api/v1/requirements/projects/" + project["id"],
            json={"raw_text": "The user sees confirmation."},
        )
        assert response.status_code == 201
        assert response.json()["req_code"] == "REQ-001"
        approval = client.get("/api/v1/approvals", params={"project_id": project["id"]}).json()[0]
        decided = client.post(
            "/api/v1/approvals/" + approval["id"] + "/approve",
            json={"decided_by": "tester"},
        )
        assert decided.status_code == 200
        assert decided.json()["decided_at"] is not None
        assert (
            client.get("/api/v1/requirements/" + response.json()["id"]).json()["status"]
            == "APPROVED"
        )


def test_ambiguity_requires_revision_and_supersedes_old_approval(client: TestClient) -> None:
    project = client.post("/api/v1/projects", json={"name": "Revision project"}).json()
    first = client.post(
        "/api/v1/requirements/projects/" + project["id"],
        json={"raw_text": "The page should load quickly."},
    ).json()
    assert first["status"] == "NEEDS_CLARIFICATION"
    old = client.get("/api/v1/approvals", params={"project_id": project["id"]}).json()[0]
    blocked = client.post(
        "/api/v1/approvals/" + old["id"] + "/approve", json={"decided_by": "tester"}
    )
    assert blocked.status_code == 409
    revised = client.post(
        "/api/v1/requirements/" + first["id"] + "/revisions",
        json={"raw_text": "The page loads within two seconds."},
    )
    assert revised.status_code == 201
    assert revised.json()["version"] == 2
    assert revised.json()["status"] == "PENDING_APPROVAL"
    assert (
        client.post(
            "/api/v1/approvals/" + old["id"] + "/approve", json={"decided_by": "tester"}
        ).status_code
        == 409
    )
    current = client.get("/api/v1/approvals", params={"project_id": project["id"]}).json()[0]
    assert current["id"] != old["id"]
    assert (
        client.post(
            "/api/v1/approvals/" + current["id"] + "/approve", json={"decided_by": "tester"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/requirements/" + first["id"]).json()["status"] == "APPROVED"


def test_tester_can_resolve_ambiguity_without_another_llm_revision(client: TestClient) -> None:
    project = client.post("/api/v1/projects", json={"name": "Clarification project"}).json()
    first = client.post(
        "/api/v1/requirements/projects/" + project["id"],
        json={"raw_text": "The page should load quickly."},
    ).json()
    old_approval = client.get("/api/v1/approvals", params={"project_id": project["id"]}).json()[0]
    endpoint = f"/api/v1/requirements/{first['id']}/clarifications"
    body = {
        "expected_version": 1,
        "resolved_by": "tester",
        "resolutions": [{"ambiguity_index": 0, "decision": "The page loads within two seconds."}],
    }
    assert client.post(endpoint, json={**body, "expected_version": 2}).status_code == 409
    assert (
        client.post(
            endpoint,
            json={**body, "resolutions": [{"ambiguity_index": 1, "decision": "Two seconds."}]},
        ).status_code
        == 422
    )
    clarified = client.post(endpoint, json=body)
    assert clarified.status_code == 201
    assert clarified.json()["version"] == 2
    assert clarified.json()["ambiguities"] == []
    assert clarified.json()["status"] == "PENDING_APPROVAL"
    assert any(
        criterion["text"] == "The page loads within two seconds."
        and criterion["source"] == "REQUIREMENT"
        for criterion in clarified.json()["acceptance_criteria"]
    )
    assert client.post(endpoint, json=body).status_code == 409
    assert (
        client.post(
            "/api/v1/approvals/" + old_approval["id"] + "/approve", json={"decided_by": "tester"}
        ).status_code
        == 409
    )
    current = client.get("/api/v1/approvals", params={"project_id": project["id"]}).json()
    assert len(current) == 1 and current[0]["id"] != old_approval["id"]
    assert (
        client.post(
            "/api/v1/approvals/" + current[0]["id"] + "/approve",
            json={"decided_by": "tester"},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/requirements/" + first["id"]).json()["status"] == "APPROVED"


def test_discovery_accepts_explicit_target_for_project(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api.routers.v1 import application_maps

    queued: list[dict[str, Any]] = []

    async def fake_enqueue(function_name: str, project_id: str, payload: dict[str, Any]) -> str:
        queued.append(payload)
        return "test-job"

    monkeypatch.setattr(application_maps, "enqueue", fake_enqueue)
    project = client.post(
        "/api/v1/projects",
        json={"name": "Target project", "application_url": "https://chatgpt.com/"},
    ).json()
    response = client.post(
        f"/api/v1/application-maps/projects/{project['id']}/discover",
        json={
            "url": "https://staging.example.test/",
            "discovery_mode": "deep",
            "selected_areas": ["dashboard"],
            "selected_modules": ["users", "reports"],
            "start_from_scratch": True,
        },
    )
    assert response.status_code == 202
    assert response.json()["job_id"] == "test-job"
    assert queued[0]["target"]["url"] == "https://staging.example.test/"
    assert queued[0]["discovery_scope"] == {
        "mode": "deep",
        "selected_auth_flow": None,
        "selected_areas": ["dashboard"],
        "selected_modules": ["users", "reports"],
    }
    assert queued[0]["start_from_scratch"] is True
    fallback = client.post(f"/api/v1/application-maps/projects/{project['id']}/discover", json={})
    assert fallback.status_code == 202
    assert queued[1]["target"]["url"] == "https://chatgpt.com/"
    assert queued[1]["start_from_scratch"] is False
    missing_resume = client.post(
        f"/api/v1/application-maps/projects/{project['id']}/discover",
        json={"resume_application_map_id": str(uuid.uuid4())},
    )
    assert missing_resume.status_code == 404
