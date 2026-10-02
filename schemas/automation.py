"""API schemas for Playwright suite generation."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class GenerateAutomationRequest(BaseModel):
    test_case_ids: list[UUID] | None = None


class BlockedCaseOut(BaseModel):
    test_case_id: UUID
    test_case_code: str
    test_case_version: int
    reason: str


class ScriptOut(BaseModel):
    script_id: UUID
    script_code: str
    test_case_id: UUID
    test_case_code: str
    test_case_version: int
    requirement_id: UUID | None = None
    requirement_version: int | None = None
    application_map_id: UUID
    application_map_version: int
    framework: str
    file_path: str
    risk_level: str
    review_status: str
    blocked_reason: str | None = None
    selector_strategy: list[dict] = Field(default_factory=list)


class SourceFileOut(BaseModel):
    path: str
    content: str


class LintOut(BaseModel):
    hardcoded_sleep: int = 0
    literal_credentials: int = 0
    xpath_fallback: int = 0
    missing_traceability: int = 0
    unsupported_blocked_selectors: int = 0
    test_only: int = 0
    missing_await: int = 0
    destructive_not_skipped: int = 0
    findings: list[dict] = Field(default_factory=list)


class VerificationOut(BaseModel):
    status: str
    tsc: str = "NOT_VERIFIED"
    playwright_list: str = "NOT_VERIFIED"
    detail: str = ""


class AutomationGenerationOut(BaseModel):
    generation_id: UUID
    created_at: datetime | None = None
    scripts: list[ScriptOut]
    blocked: list[BlockedCaseOut]
    file_tree: list[str]
    selector_summary: dict[str, int]
    lint: LintOut
    risk_level: str
    review_status: str
    approval_required: bool
    approval_ids: list[UUID] = Field(default_factory=list)
    verification: VerificationOut
    download_url: str
    vscode_url: str | None = None
    cursor_url: str | None = None
    executed: bool = False
    label: str = "Generated and reviewed — not executed"
    execution_job_id: UUID | None = None
    execution_run_id: UUID | None = None
    sources: list[SourceFileOut] = Field(default_factory=list)


class AutomationGenerationSummary(BaseModel):
    generation_id: UUID
    created_at: datetime | None = None
    risk_level: str
    review_status: str
    verification_status: str
    script_count: int
    blocked_count: int
    approval_required: bool
    executed: bool = False


class AutomationListOut(BaseModel):
    generations: list[AutomationGenerationSummary]
