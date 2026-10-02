"""
Shared enums (architecture doc Section 30/22/21/25/29). Pure Python — safe to
import from domain/, infra/db/models/, schemas/, and core/ alike
(docs/PROJECT_STRUCTURE.md point 6).
"""

from enum import StrEnum


class EvidenceSource(StrEnum):
    """Section 30 — every asserted fact must carry one of these. UNKNOWN is a
    first-class, expected value, never a fallback to avoid."""

    REQUIREMENT = "REQUIREMENT"
    OBSERVED_UI = "OBSERVED_UI"
    OBSERVED_DOM = "OBSERVED_DOM"
    OBSERVED_NETWORK = "OBSERVED_NETWORK"
    OBSERVED_CONSOLE = "OBSERVED_CONSOLE"
    TEST_EXECUTION = "TEST_EXECUTION"
    INFERENCE = "INFERENCE"
    UNKNOWN = "UNKNOWN"


class FailureClassification(StrEnum):
    """Section 16's 11-value taxonomy. Implemented here (not just in
    core/agents/failure_analysis/classification_taxonomy.py) so infra/db/models
    can reference it without importing from core/."""

    APPLICATION_DEFECT = "APPLICATION_DEFECT"
    AUTOMATION_DEFECT = "AUTOMATION_DEFECT"
    TEST_DATA_DEFECT = "TEST_DATA_DEFECT"
    ENVIRONMENT_DEFECT = "ENVIRONMENT_DEFECT"
    NETWORK_DEFECT = "NETWORK_DEFECT"
    AUTHENTICATION_DEFECT = "AUTHENTICATION_DEFECT"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    TIMEOUT = "TIMEOUT"
    FLAKY_TEST = "FLAKY_TEST"
    REQUIREMENT_MISMATCH = "REQUIREMENT_MISMATCH"
    UNKNOWN = "UNKNOWN"


class ConfidenceBand(StrEnum):
    """Section 22 thresholds: >=0.90 HIGH, 0.70-0.89 REVIEW, <0.70 BLOCK."""

    HIGH = "HIGH"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


def confidence_band(score: float) -> ConfidenceBand:
    if score >= 0.90:
        return ConfidenceBand.HIGH
    if score >= 0.70:
        return ConfidenceBand.REVIEW
    return ConfidenceBand.BLOCK


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RequirementStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class TestCaseStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    # The requirement moved to a newer version after this case was written.
    OUTDATED = "OUTDATED"


class RiskLevel(StrEnum):
    """Section 29."""

    SAFE = "SAFE"
    REVIEW = "REVIEW"
    DESTRUCTIVE = "DESTRUCTIVE"


class AutomationReviewStatus(StrEnum):
    """Static review plus the human gate for non-safe automation."""

    REVIEWED = "REVIEWED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"


class TestRunStatus(StrEnum):
    __test__ = False
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TestResultStatus(StrEnum):
    __test__ = False
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class EvidenceChannel(StrEnum):
    SCREENSHOT = "screenshot"
    VIDEO = "video"
    TRACE = "trace"
    CONSOLE_LOG = "console_log"
    NETWORK_LOG = "network_log"


class OrchestratorState(StrEnum):
    """Section 25's state machine. Milestone 1 only exercises CREATED and
    REQUIREMENTS_ANALYZED; the rest are defined now so later milestones don't
    need to touch this enum's shape."""

    CREATED = "CREATED"
    REQUIREMENTS_ANALYZED = "REQUIREMENTS_ANALYZED"
    APPLICATION_DISCOVERED = "APPLICATION_DISCOVERED"
    TEST_CASES_GENERATED = "TEST_CASES_GENERATED"
    TEST_CASES_VALIDATED = "TEST_CASES_VALIDATED"
    AUTOMATION_GENERATED = "AUTOMATION_GENERATED"
    AUTOMATION_VALIDATED = "AUTOMATION_VALIDATED"
    EXECUTION = "EXECUTION"
    FAILURE_ANALYSIS = "FAILURE_ANALYSIS"
    REPORT = "REPORT"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
