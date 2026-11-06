"""Authorized, extensible browser challenge handling.

This package intentionally detects challenges but never attempts to defeat a
live anti-bot control.  Applications opt in to test fixtures or provider test
modes through :class:`ChallengePolicy`.
"""

from .detector import ChallengeDetector
from .models import Challenge, ChallengeResult, ChallengeStatus, ChallengeType
from .orchestrator import ChallengeOrchestrator
from .policies import ChallengePolicy
from .resolver import ChallengeStrategyResolver

__all__ = [
    "Challenge",
    "ChallengeDetector",
    "ChallengeOrchestrator",
    "ChallengePolicy",
    "ChallengeResult",
    "ChallengeStatus",
    "ChallengeStrategyResolver",
    "ChallengeType",
]
