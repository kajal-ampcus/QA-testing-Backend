"""Project-scoped accounts. Passwords remain encrypted and are never returned."""
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, AnyHttpUrl, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from apps.api.dependencies import get_db_session
from infra.db.models.discovery_credential import DiscoveryCredential
from infra.db.models.project import Project
from infra.secrets.vault_client import encrypt_login_secret

router = APIRouter(prefix='/projects/{project_id}/credentials', tags=['credentials'])

class CredentialCreateRequest(BaseModel):
    label: str = Field(default='Test account',min_length=1,max_length=120)
    role: str = Field(default='User',min_length=1,max_length=120)
    username: str = Field(min_length=1,max_length=500)
    password: str = Field(min_length=1,max_length=500)
    login_url: AnyHttpUrl | None = None
    username_selector: str | None = Field(default=None,max_length=200)
    password_selector: str | None = Field(default=None,max_length=200)
    submit_selector: str | None = Field(default=None,max_length=200)
    set_as_project_default: bool = True

    @field_validator('label','role','username')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():
            raise ValueError('Must not be blank')
        return value.strip()

class CredentialUpdateRequest(CredentialCreateRequest):
    username: str | None = Field(default=None,min_length=1,max_length=500)
    password: str | None = Field(default=None,min_length=1,max_length=500)
    set_as_project_default: bool = False

    @field_validator('username')
    @classmethod
    def optional_username(cls,value):
        return value

    @model_validator(mode='after')
    def paired_credentials(self):
        if (self.username is None) != (self.password is None):
            raise ValueError('Supply both username and password to replace login credentials')
        if self.username is None and any((self.login_url,self.username_selector,self.password_selector,self.submit_selector)):
            raise ValueError('Supply username and password when changing login settings')
        return self

class CredentialCreateResponse(BaseModel):
    credential_ref: str
    label: str
    role: str
    is_default: bool
    created_at: datetime


def response(row,project):
    return CredentialCreateResponse(credential_ref=row.credential_ref,label=row.label,role=row.role,is_default=project.credential_ref==row.credential_ref,created_at=row.created_at)

async def project_for(session,project_id):
    project=await session.get(Project,project_id)
    if project is None:
        raise HTTPException(404,'Project not found')
    return project

async def account_for(session,project_id,ref):
    row=await session.scalar(select(DiscoveryCredential).where(DiscoveryCredential.project_id==project_id,DiscoveryCredential.credential_ref==ref,DiscoveryCredential.active.is_(True)).with_for_update())
    if row is None:
        raise HTTPException(404,'Active account not found in this project')
    return row

def secret_for(body):
    secret=body.model_dump(mode='json',include={'username','password','login_url','username_selector','password_selector','submit_selector'},exclude_none=True)
    try:
        return encrypt_login_secret(secret)
    except RuntimeError as exc:
        raise HTTPException(503,'Credential encryption is not configured. Configure CREDENTIAL_ENCRYPTION_KEY on the backend.') from exc

@router.get('',response_model=list[CredentialCreateResponse])
async def list_credentials(project_id:uuid.UUID,session:AsyncSession=Depends(get_db_session)):
    project=await project_for(session,project_id)
    rows=await session.scalars(select(DiscoveryCredential).where(DiscoveryCredential.project_id==project_id,DiscoveryCredential.active.is_(True)).order_by(DiscoveryCredential.created_at))
    return [response(row,project) for row in rows]

@router.post('',response_model=CredentialCreateResponse,status_code=201)
async def create_credential(project_id:uuid.UUID,body:CredentialCreateRequest,session:AsyncSession=Depends(get_db_session)):
    project=await project_for(session,project_id)
    row=DiscoveryCredential(project_id=project_id,credential_ref=f'cred:{uuid.uuid4()}',label=body.label,role=body.role,encrypted_secret=secret_for(body))
    session.add(row)
    if body.set_as_project_default:
        project.credential_ref=row.credential_ref
    await session.commit()
    await session.refresh(row)
    return response(row,project)

@router.post('/{credential_ref}/revisions',response_model=CredentialCreateResponse,status_code=201)
async def revise_credential(project_id:uuid.UUID,credential_ref:str,body:CredentialUpdateRequest,session:AsyncSession=Depends(get_db_session)):
    project=await project_for(session,project_id)
    old=await account_for(session,project_id,credential_ref)
    row=DiscoveryCredential(project_id=project_id,credential_ref=f'cred:{uuid.uuid4()}',label=body.label,role=body.role,encrypted_secret=secret_for(body) if body.username is not None else old.encrypted_secret)
    old.active=False
    session.add(row)
    if project.credential_ref==old.credential_ref or body.set_as_project_default:
        project.credential_ref=row.credential_ref
    await session.commit()
    await session.refresh(row)
    return response(row,project)

@router.post('/{credential_ref}/default',response_model=CredentialCreateResponse)
async def set_default_credential(project_id:uuid.UUID,credential_ref:str,session:AsyncSession=Depends(get_db_session)):
    project=await project_for(session,project_id)
    row=await account_for(session,project_id,credential_ref)
    project.credential_ref=row.credential_ref
    await session.commit()
    return response(row,project)
