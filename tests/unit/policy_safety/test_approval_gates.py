from domain.enums import RiskLevel
from core.policy_safety.approval_gates import requires_approval


def test_requirement_and_test_cases_always_need_approval() -> None:
    assert requires_approval("requirement_interpretation", RiskLevel.SAFE, "development")
    assert requires_approval("test_case_set", RiskLevel.SAFE, "development")
    assert requires_approval("defect_filing")


def test_automation_needs_approval_for_destructive_or_production() -> None:
    assert not requires_approval("automation_script", RiskLevel.SAFE, "development")
    assert requires_approval("automation_script", RiskLevel.DESTRUCTIVE, "development")
    assert requires_approval("automation_script", RiskLevel.SAFE, "production")
