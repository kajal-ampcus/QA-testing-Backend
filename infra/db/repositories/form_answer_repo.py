"""Read and write encrypted form answers without exposing them to checkpoints."""

import uuid

from sqlalchemy import select

from infra.db.models.discovery_form_answer import DiscoveryFormAnswer
from infra.db.session import AsyncSessionLocal
from infra.secrets.vault_client import decrypt_json_secret, encrypt_json_secret


async def get_form_answers(
    project_id: uuid.UUID, page_key: str, form_key: str
) -> dict[str, str] | None:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(DiscoveryFormAnswer).where(
                    DiscoveryFormAnswer.project_id == project_id,
                    DiscoveryFormAnswer.page_key == page_key,
                    DiscoveryFormAnswer.form_key == form_key,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    payload = decrypt_json_secret(row.encrypted_values)
    raw = payload.get("fields")
    if not isinstance(raw, dict):
        return None
    return {str(key): str(value) for key, value in raw.items() if isinstance(value, str) and value}


async def save_form_answers(
    project_id: uuid.UUID,
    page_key: str,
    form_key: str,
    values: dict[str, str],
) -> None:
    encrypted = encrypt_json_secret({"fields": values})
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(DiscoveryFormAnswer).where(
                    DiscoveryFormAnswer.project_id == project_id,
                    DiscoveryFormAnswer.page_key == page_key,
                    DiscoveryFormAnswer.form_key == form_key,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                DiscoveryFormAnswer(
                    project_id=project_id,
                    page_key=page_key,
                    form_key=form_key,
                    encrypted_values=encrypted,
                )
            )
        else:
            row.encrypted_values = encrypted
        await session.commit()
