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
async def test_submitted_login_creates_account_and_resumes_with_reference(monkeypatch):
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
    await api.submit_input_request(app_map.id, "login-input", api.SubmitInputRequestBody(fields=[
        api.InputFieldAnswer(key="textbox:username", value="qa-user"),
        api.InputFieldAnswer(key="textbox:password", value="private-password"),
    ]), Session())

    ref = added[0].credential_ref
    assert project.credential_ref == ref
    assert queued[0]["target"]["credential_ref"] == ref
    assert queued[0]["resume_application_map_id"] == str(app_map.id)
    assert saved[0]["credential_ref"] == ref
    assert app_map.discovery_checkpoint["input_requests"][0]["credential_ref"] == ref
    assert "private-password" not in str(app_map.discovery_checkpoint)
    assert "private-password" not in str(queued)

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
