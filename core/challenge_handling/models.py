"""Framework-neutral contracts for challenge detection and handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ChallengeType(StrEnum):
    UNKNOWN = "UNKNOWN"
    NUMERIC = "NUMERIC"
    ALPHABETIC = "ALPHABETIC"
    ALPHANUMERIC = "ALPHANUMERIC"
    ARITHMETIC = "ARITHMETIC"
    TEXT_IMAGE = "TEXT_IMAGE"
    AUDIO = "AUDIO"
    IMAGE_SELECTION = "IMAGE_SELECTION"
    CHECKBOX = "CHECKBOX"
    SLIDER = "SLIDER"
    PUZZLE = "PUZZLE"
    CUSTOM_CAPTCHA = "CUSTOM_CAPTCHA"
    EMAIL_OTP = "EMAIL_OTP"
    SMS_OTP = "SMS_OTP"
    TOTP = "TOTP"
    PUSH_MFA = "PUSH_MFA"
    WEBAUTHN = "WEBAUTHN"


class ChallengeStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    TIMEOUT = "TIMEOUT"
    UNSUPPORTED = "UNSUPPORTED"
    MANUAL_INTERVENTION_REQUIRED = "MANUAL_INTERVENTION_REQUIRED"


class ChallengeStrategy(StrEnum):
    PROVIDER_TEST_MODE = "provider_test_mode"
    STAGING_TEST_FIXTURE = "staging_test_fixture"
    AUTH_TEST_INTEGRATION = "auth_test_integration"
    HUMAN_INTERVENTION = "human_intervention"
    BLOCK = "block"


@dataclass(frozen=True)
class Challenge:
    challenge_type: ChallengeType
    provider: str | None
    confidence: float
    signals: tuple[str, ...] = ()
    input_locator: str | None = None
    frame_url: str | None = None


@dataclass(frozen=True)
class ChallengeResult:
    status: ChallengeStatus
    challenge: Challenge
    strategy: ChallengeStrategy
    verified: bool = False
    attempts: int = 0
    duration_ms: int | None = None
    detail: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
