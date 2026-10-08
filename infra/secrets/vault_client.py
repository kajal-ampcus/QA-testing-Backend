"""Encrypted database-backed secret storage for backend deployments."""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values
from sqlalchemy import select

from core.agents.application_discovery.form_inputs import is_ephemeral_field
from infra.db.models.discovery_credential import DiscoveryCredential
from infra.db.session import AsyncSessionLocal

_PASSWORD_NAME = re.compile(r"\b(password|passphrase)\b", re.I)
_USERNAME_NAME = re.compile(
    r"\b(email|e-mail|username|user name|user id|userid|login|employee id|staff id)\b",
    re.I,
)


def _cipher() -> Fernet:
    raw = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if raw is None:
        raw = dotenv_values(Path(__file__).resolve().parents[2] / ".env").get(
            "CREDENTIAL_ENCRYPTION_KEY"
        )
    if not raw:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is invalid") from exc


def encrypt_json_secret(payload: dict[str, Any]) -> str:
    return _cipher().encrypt(json.dumps(payload).encode()).decode()


def decrypt_json_secret(token: str) -> dict[str, Any]:
    try:
        payload = json.loads(_cipher().decrypt(token.encode()).decode())
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Stored secret cannot be decrypted") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Stored secret cannot be decrypted")
    return payload


def encrypt_login_secret(secret: dict[str, Any]) -> str:
    return encrypt_json_secret(secret)


def _login_fields(secret: dict[str, Any]) -> list[dict[str, str]]:
    fields = secret.get("fields")
    if not isinstance(fields, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in fields:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and name and isinstance(value, str) and value:
            cleaned.append({"name": name, "value": value})
    return cleaned


async def get_login_secret(ref: str, project_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Resolve and decrypt one active credential only inside the browser gateway.

    When project_id is given, a reference owned by another project does not resolve.
    """
    query = select(DiscoveryCredential).where(
        DiscoveryCredential.credential_ref == ref,
        DiscoveryCredential.active.is_(True),
    )
    if project_id is not None:
        query = query.where(DiscoveryCredential.project_id == project_id)
    async with AsyncSessionLocal() as session:
        credential = (await session.execute(query)).scalar_one_or_none()
    if credential is None:
        raise RuntimeError(f"Credential reference is not configured for this project: {ref}")
    try:
        secret = decrypt_json_secret(credential.encrypted_secret)
    except RuntimeError as exc:
        raise RuntimeError("Stored credential cannot be decrypted") from exc
    if not isinstance(secret, dict) or not all(
        isinstance(secret.get(key), str) and secret[key] for key in ("username", "password")
    ):
        raise RuntimeError(f"Credential reference has invalid login data: {ref}")
    values: dict[str, Any] = {key: value for key, value in secret.items() if isinstance(value, str)}
    fields = _login_fields(secret)
    if fields:
        values["fields"] = fields
    # Role is not a secret; it is required to click Employee/Admin radios on
    # login forms such as Cafinity before the matching ID field appears.
    if credential.role:
        values["account_role"] = credential.role
    return values


async def merge_login_fields(
    ref: str, project_id: uuid.UUID, updates: list[dict[str, str]]
) -> None:
    """Add named field values to an account secret. Checkpoints never receive them."""
    query = select(DiscoveryCredential).where(
        DiscoveryCredential.credential_ref == ref,
        DiscoveryCredential.project_id == project_id,
        DiscoveryCredential.active.is_(True),
    )
    async with AsyncSessionLocal() as session:
        credential = (await session.execute(query)).scalar_one_or_none()
        if credential is None:
            raise RuntimeError(f"Credential reference is not configured for this project: {ref}")
        secret = decrypt_json_secret(credential.encrypted_secret)
        merged = {
            item["name"].casefold(): item for item in _login_fields(secret)
        }
        for item in updates:
            name = str(item.get("name") or "").strip()
            value = item.get("value")
            if not name or not isinstance(value, str) or not value or is_ephemeral_field(name):
                continue
            merged[name.casefold()] = {"name": name, "value": value}
            if _PASSWORD_NAME.search(name):
                secret["password"] = value
            elif _USERNAME_NAME.search(name):
                secret["username"] = value
        secret["fields"] = list(merged.values())
        credential.encrypted_secret = encrypt_login_secret(secret)
        await session.commit()
