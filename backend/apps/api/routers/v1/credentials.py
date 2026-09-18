"""Backend-only onboarding for encrypted discovery login credentials."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db_session
from infra.db.models.discovery_credential import DiscoveryCredential
from infra.db.models.project import Project
from infra.secrets.vault_client import encrypt_login_secret

router = APIRouter(prefix="/projects/{project_id}/credentials", tags=["credentials"])


class CredentialCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=500)
    password: str = Field(min_length=1, max_length=500)
    username_selector: str | None = Field(default=None, max_length=200)
    password_selector: str | None = Field(default=None, max_length=200)
    submit_selector: str | None = Field(default=None, max_length=200)
    set_as_project_default: bool = True


class CredentialCreateResponse(BaseModel):
    credential_ref: str


@router.post("", response_model=CredentialCreateResponse, status_code=201)
async def create_credential(
    project_id: uuid.UUID,
    body: CredentialCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> CredentialCreateResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    credential_ref = f"cred:{uuid.uuid4()}"
    secret = body.model_dump(exclude={"set_as_project_default"}, exclude_none=True)
    session.add(
        DiscoveryCredential(
            project_id=project_id,
            credential_ref=credential_ref,
            encrypted_secret=encrypt_login_secret(secret),
        )
    )
    if body.set_as_project_default:
        project.credential_ref = credential_ref
    await session.commit()
    return CredentialCreateResponse(credential_ref=credential_ref)
