import json
from io import BytesIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from apps.api.routers.v1 import automation as api
from core.agents.automation_generation.artifacts import build_zip, list_files, read_sources
from core.agents.automation_generation import templates
from core.agents.automation_generation.standalone import source_overrides


@pytest.mark.asyncio
@pytest.mark.parametrize("include", [False, True])
async def test_download_has_separate_local_json_with_saved_values_without_db_dependencies(monkeypatch, tmp_path, include):
    from infra.db.models.project import Project
    project_id, generation_id, case_id = uuid4(), uuid4(), uuid4()
    suite = tmp_path / "suite"
    (suite / "fixtures").mkdir(parents=True)
    (suite / "fixtures/auth.ts").write_text(templates.render_auth(base_url='"https://example.test"'), encoding="utf-8")
    (suite / "manifest.json").write_text(json.dumps({"application_url": "https://example.test", "input_bindings": [
        {"key": "name-key", "case_code": "TC-001", "field": "Name", "data_key": "name", "allow_empty": False},
        {"key": "blank-key", "case_code": "TC-001", "field": "Subject", "data_key": "subject", "explicit_value": True},
        {"key": "captcha-key", "case_code": "TC-001", "field": "CAPTCHA", "data_key": "credential:TEST_CAPTCHA"},
    ]}))
    script = NS(suite_summary={}, framework="playwright", file_path="tests/en/tc001.spec.ts", test_case_id=case_id, test_case_version=2)
    project = NS(id=project_id, credential_ref="cred:account")
    case = NS(current_version=2, credential_ref="cred:account")
    session = NS(get=AsyncMock(side_effect=lambda model, identity: project if model is Project else case))
    monkeypatch.setattr(api, "_require_generation", AsyncMock(return_value=[script]))
    monkeypatch.setattr(api, "artifact_root", lambda: tmp_path)
    monkeypatch.setattr(api, "generation_dir", lambda *args: suite)
    monkeypatch.setattr(api, "ApiSettings", lambda: NS(allow_credential_export=True, api_key="", environment="development"))
    monkeypatch.setattr(api, "_validate_case_credentials", AsyncMock())
    monkeypatch.setattr("infra.test_data_service.runtime_form_values", AsyncMock(return_value={
        "name-key": {"mode": "value", "value": "Provided form name", "source": "assigned"}}))
    resolver = AsyncMock(return_value={"username": "saved-user", "password": "saved-password",
        "login_url": "/en/login", "fields": [{"name": "Organization", "value": "Saved org"}, {"name": "CAPTCHA", "value": "expired"}]})
    monkeypatch.setattr("core.tool_gateway.secret_resolver.resolve_login", resolver)
    response = await api.download_generation(project_id, generation_id, include, None, session)
    with ZipFile(BytesIO(response.body)) as archive:
        names = set(archive.namelist())
        assert {"credentials/login-credentials.json", "credentials/test-data.json"} <= names
        data = json.loads(archive.read("credentials/test-data.json"))
        assert data["schema_version"] == 2
        assert data["test_cases"]["TC-001"]["inputs"]["Name"]["value"] == "Provided form name"
        assert "blank-key" not in str(data)
        assert "captcha-key" not in str(data)
        account = json.loads(archive.read("credentials/login-credentials.json"))["accounts"][script.file_path]
        assert account["username"] == ("saved-user" if include else "")
        assert account["password"] == ("saved-password" if include else "")
        assert account["fields"] == ({"Organization": "Saved org"} if include else {})
        auth = archive.read("fixtures/auth.ts").decode()
        assert "credentials/login-credentials.json" in auth
        assert "saved-password" not in auth
        assert "credentials/test-data.json" in archive.read("data/execution-inputs.ts").decode()
    assert resolver.await_count == int(include)


def test_private_json_is_not_exposed_or_copied_from_artifact_folder(tmp_path):
    suite = tmp_path / "suite"
    (suite / "credentials").mkdir(parents=True)
    for name in ["login-credentials.json", "test-data.json"]:
        (suite / "credentials" / name).write_text('"private value"')
    assert not list_files(suite)
    assert not read_sources(suite)
    with ZipFile(BytesIO(build_zip(tmp_path, suite))) as archive:
        assert not archive.namelist()


@pytest.mark.asyncio
async def test_production_export_still_requires_authenticated_key(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_require_generation", AsyncMock(return_value=[NS(suite_summary={}, framework="playwright")]))
    monkeypatch.setattr(api, "artifact_root", lambda: tmp_path)
    monkeypatch.setattr(api, "generation_dir", lambda *args: tmp_path)
    monkeypatch.setattr(api, "ApiSettings", lambda: NS(allow_credential_export=True, api_key="", environment="production"))
    with pytest.raises(HTTPException) as error:
        await api.download_generation(uuid4(), uuid4(), True, None, NS())
    assert error.value.status_code == 403


def test_existing_auth_source_is_upgraded_without_replacing_custom_code(tmp_path):
    (tmp_path / "fixtures").mkdir()
    auth = templates.render_auth(base_url='"https://example.test"') + "\n// Custom addition preserved\n"
    (tmp_path / "fixtures/auth.ts").write_text(auth, encoding="utf-8")
    upgraded = source_overrides(tmp_path, {"application_url": "https://example.test"})
    assert "Custom addition preserved" in upgraded["fixtures/auth.ts"]
    assert "entry is [string, string]" in upgraded["fixtures/auth.ts"]
    assert "core.agents.test_execution.captcha_solve" not in upgraded["fixtures/auth.ts"]


@pytest.mark.asyncio
async def test_form_preview_shows_assigned_data_missing_fields_and_redacts_private_values(monkeypatch, tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"input_bindings": [
        {"key": "saved", "case_code": "TC-012", "field": "Name *"},
        {"key": "missing", "case_code": "TC-025", "field": "Subject *"},
        {"key": "private", "case_code": "TC-001", "field": "Private form value", "sensitive": True},
        {"key": "login", "case_code": "TC-001", "field": "Username", "data_key": "credential:TEST_USERNAME"},
    ]}))
    monkeypatch.setattr(api, "_require_generation", AsyncMock(return_value=[]))
    monkeypatch.setattr(api, "artifact_root", lambda: tmp_path)
    monkeypatch.setattr(api, "generation_dir", lambda *args: tmp_path)
    monkeypatch.setattr("infra.test_data_service.runtime_form_values", AsyncMock(return_value={
        "saved": {"mode": "value", "value": "Assigned feedback name", "source": "assigned"},
        "private": {"mode": "value", "value": "private-text", "source": "assigned"}}))
    result = await api.generation_form_inputs(uuid4(), uuid4(), NS())
    document = json.loads(result["content"])
    assert document["test_cases"]["TC-012"]["inputs"]["Name"]["value"] == "Assigned feedback name"
    assert document["test_cases"]["TC-025"]["inputs"]["Subject"]["value"] is None
    assert "private-text" not in result["content"]
    assert not any(input["binding"] == "login" for case in document["test_cases"].values() for input in case["inputs"].values())
    assert result["saved_fields"] == 2
    assert result["missing_fields"] == 1


@pytest.mark.asyncio
async def test_refresh_materializes_current_json_without_exposing_it_in_source_preview(monkeypatch, tmp_path):
    from fastapi.responses import Response
    document = {"credentials/test-data.json": json.dumps({"values": {"field": {"mode": "value", "value": "Latest assigned value"}}}),
                "credentials/login-credentials.json": json.dumps({"accounts": {}})}
    payload = build_zip(tmp_path, tmp_path, config=document)
    downloader = AsyncMock(return_value=Response(content=payload))
    monkeypatch.setattr(api, "download_generation", downloader)
    monkeypatch.setattr(api, "artifact_root", lambda: tmp_path)
    monkeypatch.setattr(api, "generation_dir", lambda *args: tmp_path)
    project, generation, session = uuid4(), uuid4(), NS()
    response = await api.materialize_standalone_inputs(project, generation, False, None, session)
    assert response.status_code == 204
    assert json.loads((tmp_path / "credentials/test-data.json").read_text())["values"]["field"]["value"] == "Latest assigned value"
    downloader.assert_awaited_once_with(project, generation, False, None, session)
    assert all(item["path"] not in document for item in read_sources(tmp_path))
    assert "document.schema_version !== 2" in (tmp_path / "data/execution-inputs.ts").read_text()


def test_readable_fields_keep_cases_versions_and_repeated_steps_separate():
    from core.agents.automation_generation.standalone import form_input_document
    bindings = [
        {"key": "TC-012:v1:step2", "case_code": "TC-012", "field": "Name *", "step_number": 2},
        {"key": "TC-012:v1:step4", "case_code": "TC-012", "field": "Name *", "step_number": 4},
        {"key": "TC-016:v1:step2", "case_code": "TC-016", "field": "Name *"},
        {"key": "TC-012:v2:step2", "case_code": "TC-012", "field": "Name *"},
    ]
    resolved = {bindings[0]["key"]: {"value": "Positive name", "mode": "value", "source": "assigned"},
                bindings[2]["key"]: {"value": "", "mode": "empty", "source": "override"}}
    document = form_input_document({"input_bindings": bindings}, resolved)
    cases = document["test_cases"]
    assert cases["TC-012"]["inputs"]["Name"]["value"] == "Positive name"
    assert cases["TC-012"]["inputs"]["Name (step 4)"]["value"] is None
    assert cases["TC-016"]["inputs"]["Name"]["value"] == ""
    assert cases["TC-016"]["inputs"]["Name"]["mode"] == "empty"
    assert cases["TC-012 (v2)"]["version"] == 2
    assert cases["TC-012 (v2)"]["inputs"]["Name"]["binding"] == "TC-012:v2:step2"
