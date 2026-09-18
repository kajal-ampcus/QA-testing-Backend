"""Credential values are encrypted before persistent storage."""

import json

import pytest
from cryptography.fernet import Fernet

from infra.secrets.vault_client import encrypt_login_secret


def test_login_secret_is_encrypted(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key.decode())
    secret = {"username": "tester@example.test", "password": "secret"}

    encrypted = encrypt_login_secret(secret)

    assert "secret" not in encrypted
    assert json.loads(Fernet(key).decrypt(encrypted.encode())) == secret
