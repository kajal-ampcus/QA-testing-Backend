"""API-facing report: deterministic metrics, optional narrative kept separate."""

from typing import Any

from pydantic import BaseModel, Field


class ReportMetrics(BaseModel):
    requirements: int
    approved_requirements: int
    application_maps: int
    test_cases: int
    test_cases_by_status: dict[str, int] = Field(default_factory=dict)
    agent_runs: int


class ReportResponse(BaseModel):
    project_id: str
    metrics: ReportMetrics
    narrative: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)
