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


class ExecutionCancelResponse(BaseModel):
    job_id: str
    status: str


class LiveExecutionOut(BaseModel):
    project_id: UUID | None = None
    run_id: UUID | None = None


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


class TestResultOut(BaseModel):
    id: UUID
    automation_script_id: UUID | None = None
    spec_path: str
    status: str
    assertion: AssertionOut
    evidence: EvidenceOut
    duration_ms: int | None = None
    error_message: str | None = None


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


class ExecutionReportCounts(BaseModel):
    total: int
    passed: int
    failed: int
    skipped: int
    error: int
    pass_rate: float
    duration_ms: int


class ExecutionReportResult(BaseModel):
    id: UUID
    test_case_code: str
    title: str
    requirement_code: str
    status: str
    expected: str
    actual: str
    duration_ms: int | None = None
    error_message: str | None = None
    evidence: list[str] = Field(default_factory=list)


class ExecutionReportOut(BaseModel):
    run_id: UUID
    project_id: UUID
    generation_id: UUID
    environment: str
    base_url: str | None = None
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    counts: ExecutionReportCounts
    results: list[ExecutionReportResult] = Field(default_factory=list)
