"""Login values supplied during discovery can become browser credentials."""

import uuid

from cryptography.fernet import Fernet
import pytest

from apps.api.routers.v1.application_maps import _credential_from_login_form
from infra.secrets.vault_client import decrypt_json_secret


def test_login_input_creates_encrypted_opaque_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    fields = {
        "textbox:email": {"key": "textbox:email", "name": "Email", "input_type": "email"},
        "textbox:password": {"key": "textbox:password", "name": "Password", "input_type": "password"},
    }

    credential = _credential_from_login_form(
        uuid.uuid4(), fields, {"textbox:email": "qa@example.test", "textbox:password": "not-plain"},
        login_url="https://example.test/en/login",
    )
    assert credential is not None
    assert credential.credential_ref.startswith("cred:")
    assert "not-plain" not in credential.encrypted_secret
    secret = decrypt_json_secret(credential.encrypted_secret)
    assert secret["username"] == "qa@example.test"
    assert secret["password"] == "not-plain"
    assert secret["login_url"] == "https://example.test/en/login"


@pytest.mark.asyncio
@pytest.mark.parametrize("save_account", [True, False])
async def test_submitted_login_creates_account_and_resumes_with_reference(monkeypatch, save_account):
    from types import SimpleNamespace
    from apps.api.routers.v1 import application_maps as api
    from infra.db.models.application_map import ApplicationMap
    from infra.db.models.project import Project

    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    project = Project(id=uuid.uuid4(), name="Test", credential_ref=None)
    request = {
        "id": "login-input", "kind": "login", "status": "pending",
        "page_key": "/en/login", "form_key": "login-form",
        "page_url": "https://example.test/en/login", "credential_ref": None,
        "fields": [
            {"key": "textbox:username", "name": "Username", "required": True},
            {"key": "textbox:password", "name": "Password", "input_type": "password", "required": True},
        ],
    }
    app_map = SimpleNamespace(id=uuid.uuid4(), project_id=project.id,
        base_url="https://example.test/en", discovery_checkpoint={
            "configuration": {"mode": "guided", "credential_ref": None},
            "input_requests": [request],
        })
    added, saved, queued = [], [], []
    temporary = {}

    class Pool:
        async def set(self, key, value, ex):
            temporary[key] = (value, ex)

    async def pool():
        return Pool()

    class Session:
        async def get(self, model, identity):
            return app_map if model is ApplicationMap else project

        def add(self, row):
            added.append(row)

        async def commit(self):
            pass

    async def save(*args, **kwargs):
        saved.append(kwargs)

    async def claim(*args):
        return "job-login"

    async def enqueue(function, project_id, payload, **kwargs):
        queued.append(payload)
        return "job-login"

    monkeypatch.setattr(api, "save_form_answers", save)
    monkeypatch.setattr(api, "_claim_discovery_slot", claim)
    monkeypatch.setattr(api, "enqueue", enqueue)
    monkeypatch.setattr(api, "get_arq_pool", pool)
    response = await api.submit_input_request(app_map.id, "login-input", api.SubmitInputRequestBody(approve_submission=True, save_account=save_account, fields=[
        api.InputFieldAnswer(key="textbox:username", value="qa-user"),
        api.InputFieldAnswer(key="textbox:password", value="private-password"),
    ]), Session())

    ref = response.credential_ref
    if save_account:
        assert added[0].credential_ref == ref
        assert not temporary
    else:
        assert not added
        encrypted, ttl = temporary[f"login:{project.id}:{ref}"]
        assert "private-password" not in encrypted
        assert decrypt_json_secret(encrypted)["password"] == "private-password"
        assert ttl == 86400
    assert project.credential_ref is None
    assert response.credential_ref == ref
    assert response.selected_branches
    assert app_map.discovery_checkpoint["configuration"]["credential_ref"] == ref
    assert not saved
    assert app_map.discovery_checkpoint["input_requests"][0]["credential_ref"] == ref
    assert "private-password" not in str(app_map.discovery_checkpoint)
    assert "private-password" not in str(queued)


@pytest.mark.asyncio
async def test_login_choice_only_accepts_observed_option(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from apps.api.routers.v1 import application_maps as api
    request = {"id": "choose", "kind": "login_choice", "status": "pending", "navigation_path": [],
        "choices": [{"id": "supplier", "label": "Supplier", "step": {"role": "tab", "name": "Supplier"}}]}
    app_map = SimpleNamespace(status="PARTIAL", discovery_checkpoint={"input_requests": [request]})
    class Session:
        async def get(self, *_args): return app_map
        async def commit(self): pass
    with pytest.raises(HTTPException) as error:
        await api.submit_input_request(uuid.uuid4(), "choose", api.SubmitInputRequestBody(approve_submission=True, choice_id="invented"), Session())
    assert error.value.status_code == 422
    response = await api.submit_input_request(uuid.uuid4(), "choose", api.SubmitInputRequestBody(approve_submission=True, choice_id="supplier"), Session())
    assert response.selected_branches
    assert app_map.discovery_checkpoint["jobs"][0]["path"][0]["name"] == "Supplier"
    assert app_map.discovery_checkpoint["input_requests"][0]["selected_choice"]["label"] == "Supplier"

def test_non_password_login_input_does_not_create_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    fields = {"textbox:email": {"key": "textbox:email", "name": "Email"}}

    assert (
        _credential_from_login_form(
            uuid.uuid4(),
            fields,
            {"textbox:email": "qa@example.test"},
            login_url="https://example.test/login",
        )
        is None
    )
