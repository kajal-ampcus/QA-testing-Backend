"""Verdict contract for Test Case Validation — checks stay separate from confidence."""

from pydantic import BaseModel, Field


class ValidationVerdict(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
