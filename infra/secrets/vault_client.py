"""Encrypted database-backed secret storage for backend deployments."""

import json
import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values
from sqlalchemy import select

from infra.db.models.discovery_credential import DiscoveryCredential
from infra.db.session import AsyncSessionLocal


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


def encrypt_login_secret(secret: dict[str, str]) -> str:
    return _cipher().encrypt(json.dumps(secret).encode()).decode()


async def get_login_secret(ref: str, project_id: uuid.UUID | None = None) -> dict[str, str]:
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
        secret = json.loads(_cipher().decrypt(credential.encrypted_secret.encode()).decode())
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Stored credential cannot be decrypted") from exc
    if not isinstance(secret, dict) or not all(
        isinstance(secret.get(key), str) and secret[key] for key in ("username", "password")
    ):
        raise RuntimeError(f"Credential reference has invalid login data: {ref}")
    values = {key: value for key, value in secret.items() if isinstance(value, str)}
    # Role is not a secret; it is required to click Employee/Admin radios on
    # login forms such as Cafinity before the matching ID field appears.
    if credential.role:
        values["account_role"] = credential.role
    return values
