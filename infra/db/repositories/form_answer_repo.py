"""Read and write encrypted form answers without exposing them to checkpoints."""

import uuid
from typing import Any

from sqlalchemy import delete, select

from core.agents.application_discovery.form_inputs import (
    is_ephemeral_field,
    is_password_field,
    mask_saved_value,
    split_saved_values,
)
from infra.db.models.discovery_form_answer import DiscoveryFormAnswer
from infra.db.session import AsyncSessionLocal
from infra.secrets.vault_client import decrypt_json_secret, encrypt_json_secret


def credential_scope(credential_ref: str | None) -> str:
    return credential_ref or ""


def select_answer_scope(
    scopes: list[str], credential_ref: str | None, *, login: bool
) -> str | None:
    """Prefer this account. Non-login forms may use the project-wide profile."""
    wanted = credential_scope(credential_ref)
    if wanted in scopes:
        return wanted
    if not login and wanted and "" in scopes:
        return ""
    return None


def row_matches(
    row: dict[str, Any],
    project_id: uuid.UUID,
    page_key: str,
    form_key: str,
    credential_ref: str | None,
    *,
    login: bool,
) -> bool:
    if row.get("project_id") != project_id:
        return False
    if row.get("page_key") != page_key or row.get("form_key") != form_key:
        return False
    scope = str(row.get("credential_scope") or "")
    return select_answer_scope([scope], credential_ref, login=login) == scope


def _durable_fields(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("fields")
    if not isinstance(raw, dict):
        return {}
    values = {
        str(key): str(value)
        for key, value in raw.items()
        if isinstance(value, str) and value and not is_ephemeral_field(str(key))
    }
    durable, _session = split_saved_values(values)
    return durable


def _session_fields(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("session_fields")
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if isinstance(value, str) and value}


def _labels(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("labels")
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if isinstance(value, str) and value}


async def _load_rows(
    project_id: uuid.UUID, page_key: str, form_key: str
) -> list[DiscoveryFormAnswer]:
    async with AsyncSessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(DiscoveryFormAnswer).where(
                        DiscoveryFormAnswer.project_id == project_id,
                        DiscoveryFormAnswer.page_key == page_key,
                        DiscoveryFormAnswer.form_key == form_key,
                    )
                )
            ).scalars()
        )


def _pick(
    rows: list[DiscoveryFormAnswer], credential_ref: str | None, *, login: bool
) -> DiscoveryFormAnswer | None:
    scopes = [row.credential_scope or "" for row in rows]
    chosen = select_answer_scope(scopes, credential_ref, login=login)
    if chosen is None:
        return None
    return next(row for row in rows if (row.credential_scope or "") == chosen)


async def get_form_answers(
    project_id: uuid.UUID,
    page_key: str,
    form_key: str,
    credential_ref: str | None = None,
    *,
    login: bool = False,
) -> dict[str, str] | None:
    row = _pick(await _load_rows(project_id, page_key, form_key), credential_ref, login=login)
    if row is None:
        return None
    return _durable_fields(decrypt_json_secret(row.encrypted_values)) or None


async def get_session_answers(
    project_id: uuid.UUID,
    page_key: str,
    form_key: str,
    credential_ref: str | None = None,
) -> dict[str, str] | None:
    row = _pick(
        await _load_rows(project_id, page_key, form_key), credential_ref, login=True
    )
    if row is None:
        return None
    values = _session_fields(decrypt_json_secret(row.encrypted_values))
    return values or None


async def save_form_answers(
    project_id: uuid.UUID,
    page_key: str,
    form_key: str,
    values: dict[str, str],
    *,
    credential_ref: str | None = None,
    labels: dict[str, str] | None = None,
    session_values: dict[str, str] | None = None,
) -> None:
    durable, discovered_session = split_saved_values(values, [
        {"key": key, "name": (labels or {}).get(key, key)} for key in values
    ])
    scope = credential_scope(credential_ref)
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(DiscoveryFormAnswer).where(
                    DiscoveryFormAnswer.project_id == project_id,
                    DiscoveryFormAnswer.credential_scope == scope,
                    DiscoveryFormAnswer.page_key == page_key,
                    DiscoveryFormAnswer.form_key == form_key,
                )
            )
        ).scalar_one_or_none()
        previous = decrypt_json_secret(row.encrypted_values) if row is not None else {}
        stored_fields = _durable_fields(previous)
        stored_fields.update(durable)
        if session_values is not None:
            stored_session = session_values
        else:
            stored_session = _session_fields(previous)
            stored_session.update(discovered_session)
        payload = {
            "fields": stored_fields,
            "session_fields": stored_session,
            "labels": {**_labels(previous), **(labels or {})},
        }
        encrypted = encrypt_json_secret(payload)
        if row is None:
            session.add(
                DiscoveryFormAnswer(
                    project_id=project_id,
                    page_key=page_key,
                    form_key=form_key,
                    credential_ref=credential_ref,
                    credential_scope=scope,
                    encrypted_values=encrypted,
                )
            )
        else:
            row.encrypted_values = encrypted
            row.credential_ref = credential_ref
        await session.commit()


async def clear_session_answers(project_id: uuid.UUID) -> None:
    """Drop one-time challenges left from an earlier authentication session."""
    async with AsyncSessionLocal() as session:
        rows = list(
            (
                await session.execute(
                    select(DiscoveryFormAnswer).where(DiscoveryFormAnswer.project_id == project_id)
                )
            ).scalars()
        )
        for row in rows:
            payload = decrypt_json_secret(row.encrypted_values)
            if not _session_fields(payload):
                continue
            payload["session_fields"] = {}
            row.encrypted_values = encrypt_json_secret(payload)
        await session.commit()


async def list_saved_inputs(project_id: uuid.UUID) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        rows = list(
            (
                await session.execute(
                    select(DiscoveryFormAnswer)
                    .where(DiscoveryFormAnswer.project_id == project_id)
                    .order_by(DiscoveryFormAnswer.updated_at.desc())
                )
            ).scalars()
        )
    listed: list[dict[str, Any]] = []
    for row in rows:
        payload = decrypt_json_secret(row.encrypted_values)
        fields = _durable_fields(payload)
        if not fields:
            continue
        labels = _labels(payload)
        listed.append(
            {
                "id": str(row.id),
                "page_key": row.page_key,
                "form_key": row.form_key,
                "credential_ref": row.credential_ref,
                "updated_at": row.updated_at,
                "fields": [
                    {
                        "key": key,
                        "name": labels.get(key) or key.split(":", 1)[-1],
                        "sensitive": is_password_field({"name": key}) or is_ephemeral_field(key),
                        "preview": mask_saved_value(key, value),
                    }
                    for key, value in fields.items()
                ],
            }
        )
    return listed


async def update_saved_input(
    project_id: uuid.UUID, answer_id: uuid.UUID, values: dict[str, str]
) -> bool:
    durable, _session = split_saved_values(values)
    async with AsyncSessionLocal() as session:
        row = await session.get(DiscoveryFormAnswer, answer_id)
        if row is None or row.project_id != project_id:
            return False
        payload = decrypt_json_secret(row.encrypted_values)
        current = _durable_fields(payload)
        current.update(durable)
        payload["fields"] = current
        payload["session_fields"] = {}
        row.encrypted_values = encrypt_json_secret(payload)
        await session.commit()
    return True


async def delete_saved_input(project_id: uuid.UUID, answer_id: uuid.UUID) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(DiscoveryFormAnswer).where(
                DiscoveryFormAnswer.id == answer_id,
                DiscoveryFormAnswer.project_id == project_id,
            )
        )
        await session.commit()
    return bool(result.rowcount)
