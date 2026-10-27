from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field

TestType = Literal["positive", "negative" , "validation", "edge"]

class FormTestDataCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    form_group: str = Field(min_length=1, max_length=120)
    test_case_id: UUID | None = None
    test_type: TestType
    test_data: dict[str, object]
    expected_result: str | None = None
    tags: list[str] = Field(default_factory=list)
    
    
class FormTestDataUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    form_group: str | None = Field(default=None, min_length=1, max_length=120)
    test_type: TestType | None = None
    test_data: dict[str, object] | None = None
    expected_result: str | None = None
    tags: list[str] | None = None
    test_case_id: UUID | None = None
    active: bool | None = None


class FormTestDataResponse(BaseModel):
    id: UUID
    project_id: UUID
    test_case_id: UUID | None = None
    label: str
    form_group: str
    test_type: str
    test_data: dict[str, object]
    expected_result: str | None
    tags: list[str]
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
    
    
