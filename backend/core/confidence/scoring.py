"""
Confidence scoring (architecture doc Section 22) — computed from evidence
agreement and check determinism, NEVER self-reported by an LLM as a free-
floating number. Also owns historical calibration: an agent's past
confidence-vs-actual-correctness (tracked via human overrides/corrections)
recalibrates its scoring over time.

Thresholds: >=0.90 proceed autonomously; 0.70-0.89 proceed but flag for
review; <0.70 block and require human decision.

Phase 0 stub.
"""

# TODO (Phase 1): def compute_confidence(evidence: list[Evidence], check_determinism: float) -> float: ...
