"""
TestRun / TestResult entities (architecture doc Section 14/26).

Pass/fail is a comparison of assertion.expected vs assertion.actual.
Evidence fields hold object-storage keys only, never binary content.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from domain.enums import EvidenceChannel, EvidenceSource, TestResultStatus, TestRunStatus


@dataclass
class AssertionRecord:
    expected: str
    actual: str
    source: EvidenceSource = EvidenceSource.TEST_EXECUTION


@dataclass
class EvidenceRefs:
    screenshot: str | None = None
    video: str | None = None
    trace: str | None = None
    console_log: str | None = None
    network_log: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {channel.value: getattr(self, channel.value) for channel in EvidenceChannel}


@dataclass
class TestResult:
    __test__ = False
    id: UUID
    test_run_id: UUID
    automation_script_id: UUID | None
    spec_path: str
    status: TestResultStatus
    assertion: AssertionRecord
    evidence: EvidenceRefs = field(default_factory=EvidenceRefs)
    duration_ms: int | None = None
    error_message: str | None = None


@dataclass
class TestRun:
    __test__ = False
    id: UUID
    project_id: UUID
    generation_id: UUID
    environment: str
    status: TestRunStatus
    job_id: str | None = None
    base_url: str | None = None
    run_destructive: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: dict[str, int] = field(default_factory=dict)
    results: list[TestResult] = field(default_factory=list)
