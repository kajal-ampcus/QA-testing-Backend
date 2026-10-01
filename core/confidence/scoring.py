"""
Confidence scoring (architecture doc Section 22) — computed from evidence
agreement and check determinism, NEVER self-reported by an LLM as a free-
floating number.

Thresholds: >=0.90 HIGH, 0.70-0.89 REVIEW, <0.70 BLOCK.
"""

from domain.enums import ConfidenceBand, confidence_band


def compute_confidence(
    *,
    evidence_count: int,
    check_pass_rate: float,
    inference_share: float = 0.0,
) -> float:
    """Combine how much evidence was cited with how many deterministic checks passed.

    `check_pass_rate` is 0–1 (fraction of structural checks that passed).
    `inference_share` is 0–1 (fraction of cited sources that were INFERENCE/UNKNOWN).
    """
    rate = min(1.0, max(0.0, check_pass_rate))
    inferred = min(1.0, max(0.0, inference_share))
    # A single cited source is enough to start; more sources raise the floor.
    agreement = min(1.0, 0.55 + 0.09 * max(0, evidence_count))
    score = (rate * 0.7) + (agreement * 0.3) - (0.25 * inferred)
    return round(min(1.0, max(0.0, score)), 3)


def band_for(
    evidence_count: int,
    check_pass_rate: float,
    inference_share: float = 0.0,
) -> ConfidenceBand:
    return confidence_band(
        compute_confidence(
            evidence_count=evidence_count,
            check_pass_rate=check_pass_rate,
            inference_share=inference_share,
        )
    )
