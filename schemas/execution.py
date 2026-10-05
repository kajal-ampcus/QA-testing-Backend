"""API-facing pydantic schemas for TestRun / TestResult / evidence refs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TriggerExecutionRequest(BaseModel):
    generation_id: UUID
    run_destructive: bool = False
    script_ids: list[UUID] | None = None


class TriggerExecutionResponse(BaseModel):
    job_id: str
    run_id: UUID
    status: str


class ExecutionJobResponse(BaseModel):
    job_id: str
    status: str
    run_id: UUID | None = None
    result: dict[str, Any] | None = None


class AssertionOut(BaseModel):
    expected: str
    actual: str
    source: str = "TEST_EXECUTION"


class EvidenceOut(BaseModel):
    screenshot: str | None = None
    video: str | None = None
    trace: str | None = None
    console_log: str | None = None
    network_log: str | None = None


class InputFieldOut(BaseModel):
    name: str
    value: str


class TestResultOut(BaseModel):
    id: UUID
    automation_script_id: UUID | None = None
    spec_path: str
    status: str
    assertion: AssertionOut
    evidence: EvidenceOut
    duration_ms: int | None = None
    error_message: str | None = None
    category: str = ""
    title: str = ""
    inputs: list[InputFieldOut] = Field(default_factory=list)
    cause: str = ""
    recommendation: str = ""


class TestRunSummaryOut(BaseModel):
    id: UUID
    generation_id: UUID
    job_id: str | None = None
    environment: str
    base_url: str | None = None
    run_destructive: bool
    status: str
    summary: dict[str, int] = Field(default_factory=dict)
    log: str | None = None
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
    result_count: int = 0


class TestRunDetailOut(TestRunSummaryOut):
    results: list[TestResultOut] = Field(default_factory=list)


class ExecutionListOut(BaseModel):
    runs: list[TestRunSummaryOut]
