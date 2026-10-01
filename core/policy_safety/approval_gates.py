"""
The structural implementation of every human-approval gate in Section 21:
requirement interpretation, validated test cases, destructive/production
automation, defect filing. This module — not any individual agent — decides
whether a given action requires approval before proceeding.

Deliberately NOT an LLM agent (Section 5: Safety/Policy was demoted from
"agent" to infrastructure so it cannot be prompt-injected around).
"""

from domain.enums import RiskLevel

# Actions that always wait for a tester, regardless of environment or risk.
_ALWAYS = frozenset(
    {
        "requirement_interpretation",
        "test_case_set",
        "defect_filing",
    }
)

# Actions that wait only when they touch a destructive control or production.
_RISK_OR_PRODUCTION = frozenset(
    {
        "automation_script",
        "destructive_action",
    }
)


def requires_approval(
    action_type: str,
    risk_level: RiskLevel | str = RiskLevel.SAFE,
    environment: str = "development",
) -> bool:
    """Return True when the orchestrator must create a pending approval row."""
    risk = RiskLevel(risk_level) if not isinstance(risk_level, RiskLevel) else risk_level
    env = environment.strip().lower()
    if action_type in _ALWAYS:
        return True
    if action_type in _RISK_OR_PRODUCTION:
        return risk != RiskLevel.SAFE or env == "production"
    if env == "production" and risk != RiskLevel.SAFE:
        return True
    return False
