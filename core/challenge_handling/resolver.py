"""Safe, deterministic strategy selection."""

from __future__ import annotations

from .models import Challenge, ChallengeStrategy, ChallengeType
from .policies import ChallengePolicy


class ChallengeStrategyResolver:
    def resolve(self, challenge: Challenge, policy: ChallengePolicy, integrations: set[str] | None = None) -> ChallengeStrategy:
        available = integrations or set()
        if not policy.enabled:
            return ChallengeStrategy.BLOCK
        if challenge.provider and policy.allow_provider_test_modes and "provider_test_mode" in available:
            return ChallengeStrategy.PROVIDER_TEST_MODE
        if challenge.challenge_type in {ChallengeType.EMAIL_OTP, ChallengeType.SMS_OTP, ChallengeType.TOTP, ChallengeType.WEBAUTHN} and policy.allow_auth_test_integrations and "auth_test_integration" in available:
            return ChallengeStrategy.AUTH_TEST_INTEGRATION
        if policy.staging_hooks_allowed() and "staging_test_fixture" in available:
            return ChallengeStrategy.STAGING_TEST_FIXTURE
        return ChallengeStrategy.HUMAN_INTERVENTION if policy.human_fallback else ChallengeStrategy.BLOCK
