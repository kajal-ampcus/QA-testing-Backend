import pytest

from core.policy_safety.destructive_action_lexicon import classify_risk
from domain.enums import RiskLevel


@pytest.mark.parametrize(
    "name",
    ["Delete employee", "Revoke access", "Reject request", "Discard draft", "Pay now", "Place order"],
)
def test_destructive_names(name: str) -> None:
    assert classify_risk("button", name) == RiskLevel.DESTRUCTIVE


def test_unlabeled_button_is_not_safe() -> None:
    assert classify_risk("button", "  ") == RiskLevel.REVIEW


@pytest.mark.parametrize("name", ["Dashboard", "Approvals", "Transfers", "Drop-down menu"])
def test_navigation_names_stay_safe(name: str) -> None:
    assert classify_risk("link", name) == RiskLevel.SAFE
