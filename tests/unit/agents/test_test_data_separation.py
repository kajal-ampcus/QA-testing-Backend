import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zipfile import ZipFile

import pytest

from core.agents.automation_generation.artifacts import build_zip, list_files
from domain.test_data_resolution import resolve_test_data
from infra.test_data_service import field_requirements, runtime_form_values


def test_override_changes_only_target_and_keeps_explicit_empty():
    baseline = {"valid": {"email": "valid@example.test", "name": "Tester"}}
    data, sources = resolve_test_data(
        {"generated": "fallback"}, baseline,
        {"valid": {"name": "Assigned"}}, {"valid": {"email": ""}},
    )
    assert data == {"generated": "fallback", "valid": {"email": "", "name": "Assigned"}}
    assert sources["valid.email"] == "override"
    assert sources["valid.name"] == "assigned"
    assert baseline["valid"]["email"] == "valid@example.test"
    assert "valid@example.test" not in str(sources)


def test_login_metadata_is_runtime_only_but_registration_is_form_data():
    state = SimpleNamespace(state_code="S1", url_pattern="/login", elements=[
        {"element_code": "E1", "name": "Email", "role": "textbox"},
    ])
    steps = [{"action": "fill", "target": {"state_code": "S1", "element_code": "E1"}, "value": "{valid.email}"}]
    fields = field_requirements(steps, {"S1": state})
    assert fields[0]["field_code"] == "S1/E1"
    assert fields[0]["field_key"] == "valid.email"
    assert fields[0]["runtime_only"]
    state.url_pattern = "/register"
    assert not field_requirements(steps, {"S1": state})[0]["runtime_only"]


def test_feedback_submit_collects_companions_and_ignores_header_login():
    state = SimpleNamespace(state_code="S1", url_pattern="/feedback", elements=[
        {"element_code": "E0", "role": "link", "name": "Login", "locator_scope": "header"},
        {"element_code": "E1", "role": "textbox", "name": "Email", "credential_binding": "username", "required": True},
        {"element_code": "E2", "role": "textbox", "name": "Message *"},
        {"element_code": "E3", "role": "textbox", "name": "Search", "locator_scope": "header"},
        {"element_code": "E4", "role": "button", "name": "Send feedback", "input_type": "submit"},
    ])
    fields = field_requirements([{"action": "click", "target": {"state_code": "S1", "element_code": "E4"}}], {"S1": state})
    assert [field["field_name"] for field in fields] == ["Email", "Message *"]
    assert not any(field["runtime_only"] for field in fields)
    assert all(field["required"] for field in fields)


def test_name_based_steps_are_mapped_without_losing_explicit_blanks():
    state = SimpleNamespace(state_code="S1", url_pattern="/feedback", elements=[
        {"element_code": "E1", "role": "textbox", "name": "Name"},
        {"element_code": "E2", "role": "textbox", "name": "Message"},
    ])
    fields = field_requirements([
        {"action": "navigate", "target": {"state_code": "S1"}},
        {"action": "fill", "target": {"element_name": "Name"}, "value": ""},
    ], {"S1": state})
    assert fields[0]["field_code"] == "S1/E1"
    assert fields[0]["explicit_value"] == ""
    assert fields[1]["field_name"] == "Message"


def test_export_never_copies_private_config_from_workspace(tmp_path):
    suite = tmp_path / "suite"
    (suite / "config").mkdir(parents=True)
    (suite / "config" / "credentials.json").write_text('secret')
    (suite / "config" / "testdata.json").write_text('private')
    (suite / "execution-inputs.json").write_text('private')
    config = {"config/testdata.json": json.dumps({"values": {}})}
    with ZipFile(BytesIO(build_zip(tmp_path, suite, config=config))) as archive:
        assert archive.namelist() == ["config/testdata.json"]
        assert "secret" not in archive.read("config/testdata.json").decode()
    assert not list_files(suite)


@pytest.mark.asyncio
async def test_runtime_uses_shared_resolver_preserves_blank_and_skips_credentials(monkeypatch):
    project_id, case_id = uuid4(), uuid4()
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id=case_id, project_id=project_id, current_version=1)),
        scalar=AsyncMock(return_value=SimpleNamespace(test_data={})),
    )
    resolver = AsyncMock(return_value=({"email": "", "name": "Assigned"}, {"email": "override"}, None))
    monkeypatch.setattr("infra.test_data_service.resolve_case_data", resolver)
    script = SimpleNamespace(test_case_id=case_id, test_case_version=1)
    bindings = [
        {"key": "email", "case_code": "TC1", "field": "Email", "data_key": "email", "allow_empty": True},
        {"key": "user", "case_code": "TC1", "field": "Username", "data_key": "credential:TEST_USERNAME"},
        {"key": "name", "case_code": "TC1", "field": "Name", "data_key": "name", "explicit_value": True},
    ]
    session.get.return_value.tc_code = "TC1"
    values = await runtime_form_values(session, project_id, [script], bindings)
    assert values == {"email": {"mode": "empty", "value": "", "source": "override"}}
    resolver.assert_awaited_once()


@pytest.mark.asyncio
async def test_credential_export_denied_without_server_authorization(monkeypatch, tmp_path):
    from fastapi import HTTPException

    from apps.api.routers.v1 import automation

    monkeypatch.setattr(automation, "_require_generation", AsyncMock(return_value=[
        SimpleNamespace(suite_summary={}, framework="playwright"),
    ]))
    monkeypatch.setattr(automation, "artifact_root", lambda: tmp_path)
    monkeypatch.setattr(automation, "generation_dir", lambda *args: tmp_path)
    monkeypatch.setattr(automation, "ApiSettings", lambda: SimpleNamespace(
        allow_credential_export=False, api_key="configured-key",
    ))
    with pytest.raises(HTTPException) as error:
        await automation.download_generation(uuid4(), uuid4(), True, "configured-key", SimpleNamespace())
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_smart_template_never_returns_inline_login_values(monkeypatch):
    from infra.test_data_service import smart_template

    map_id, case_id = uuid4(), uuid4()
    case = SimpleNamespace(id=case_id, application_map_id=map_id, tc_code="TC1")
    version = SimpleNamespace(test_data={}, category="POSITIVE", steps=[
        {"action": "fill", "target": {"state_code": "S1", "element_code": "E1"}, "value": "private-value"},
    ])
    state = SimpleNamespace(application_map_id=map_id, state_code="S1", url_pattern="/login", elements=[
        {"element_code": "E1", "name": "Password", "input_type": "password"},
    ])
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(case, version)])),
        scalars=AsyncMock(return_value=[state]),
    )
    monkeypatch.setattr("infra.test_data_service.resolve_case_data", AsyncMock(return_value=({}, {}, None)))
    result = await smart_template(session, uuid4())
    assert result["rows"][0]["source"] == "runtime"
    assert "private-value" not in json.dumps(result)
