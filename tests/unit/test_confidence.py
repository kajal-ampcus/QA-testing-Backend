from core.confidence.scoring import compute_confidence
from domain.enums import ConfidenceBand, confidence_band


def test_passing_checks_with_evidence_are_review_or_high() -> None:
    score = compute_confidence(evidence_count=3, check_pass_rate=1.0, inference_share=0.0)
    assert score >= 0.7
    assert confidence_band(score) in {ConfidenceBand.REVIEW, ConfidenceBand.HIGH}


def test_failed_checks_drop_below_autonomous_threshold() -> None:
    score = compute_confidence(evidence_count=1, check_pass_rate=0.25, inference_share=0.5)
    assert score < 0.7
