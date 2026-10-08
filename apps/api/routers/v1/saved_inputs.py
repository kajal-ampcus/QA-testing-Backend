"""Project-scoped reusable form answers. Secret values are masked."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from infra.db.repositories.form_answer_repo import (
    delete_saved_input,
    list_saved_inputs,
    update_saved_input,
)

router = APIRouter(prefix="/projects/{project_id}/saved-inputs", tags=["saved-inputs"])


class SavedInputField(BaseModel):
    key: str
    name: str
    sensitive: bool
    preview: str


class SavedInputProfile(BaseModel):
    id: str
    page_key: str
    form_key: str
    credential_ref: str | None
    updated_at: datetime | None
    fields: list[SavedInputField]


class SavedInputUpdate(BaseModel):
    fields: list[dict[str, str]] = Field(min_length=1, max_length=40)


@router.get("", response_model=list[SavedInputProfile])
async def get_saved_inputs(project_id: uuid.UUID) -> list[dict]:
    return await list_saved_inputs(project_id)


@router.patch("/{answer_id}", response_model=SavedInputProfile)
async def patch_saved_input(
    project_id: uuid.UUID, answer_id: uuid.UUID, body: SavedInputUpdate
) -> dict:
    values = {
        str(item.get("key") or ""): str(item.get("value") or "")
        for item in body.fields
        if item.get("key") and str(item.get("value") or "").strip()
    }
    if not values:
        raise HTTPException(status_code=422, detail="Enter a value to update")
    updated = await update_saved_input(project_id, answer_id, values)
    if not updated:
        raise HTTPException(status_code=404, detail="Saved input not found")
    match = next((item for item in await list_saved_inputs(project_id) if item["id"] == str(answer_id)), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Saved input not found")
    return match


@router.delete("/{answer_id}", status_code=204)
async def remove_saved_input(project_id: uuid.UUID, answer_id: uuid.UUID) -> None:
    deleted = await delete_saved_input(project_id, answer_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved input not found")
