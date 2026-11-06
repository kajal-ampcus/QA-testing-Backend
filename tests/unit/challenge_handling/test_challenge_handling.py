import pytest

from core.challenge_handling import (
    ChallengeDetector,
    ChallengeOrchestrator,
    ChallengePolicy,
    ChallengeStrategyResolver,
)
from core.challenge_handling.models import (
    Challenge,
    ChallengeStatus,
    ChallengeStrategy,
    ChallengeType,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Enter numeric CAPTCHA digits only", ChallengeType.NUMERIC),
        ("Enter alphabetic CAPTCHA letters only", ChallengeType.ALPHABETIC),
        ("Enter alphanumeric CAPTCHA", ChallengeType.ALPHANUMERIC),
        ("CAPTCHA math question: 12 + 8", ChallengeType.ARITHMETIC),
        ("Select all images containing traffic lights", ChallengeType.IMAGE_SELECTION),
    ],
)
def test_detector_classifies_multi_signal_challenges(text, expected):
    found = ChallengeDetector().detect({"text": text, "fields": [{"name": "captcha answer", "locator": "a11y:captcha"}]})
    assert found is not None
    assert found.challenge_type == expected
    assert found.confidence >= 0.55
    assert found.input_locator == "a11y:captcha"


def test_detector_identifies_provider_from_frame_metadata():
    found = ChallengeDetector().detect({"frames": [{"url": "https://challenges.cloudflare.com/turnstile"}]})
    assert found is not None
    assert found.provider == "turnstile"
    assert found.challenge_type == ChallengeType.CHECKBOX


def test_generic_challenge_is_unknown_not_a_claimed_solver_type():
    found = ChallengeDetector().detect({"text": "Security verification CAPTCHA", "fields": [{"name": "answer"}]})
    assert found is not None
    assert found.challenge_type == ChallengeType.UNKNOWN


def test_resolver_fails_closed_for_staging_hook_in_production():
    challenge = Challenge(ChallengeType.CUSTOM_CAPTCHA, None, 0.9)
    resolved = ChallengeStrategyResolver().resolve(
        challenge, ChallengePolicy(environment="production"), {"staging_test_fixture"}
    )
    assert resolved == ChallengeStrategy.HUMAN_INTERVENTION


def test_resolver_prefers_provider_test_mode_then_auth_integration():
    resolver = ChallengeStrategyResolver()
    provider = Challenge(ChallengeType.CHECKBOX, "recaptcha", 0.9)
    assert resolver.resolve(provider, ChallengePolicy(), {"provider_test_mode", "staging_test_fixture"}) == ChallengeStrategy.PROVIDER_TEST_MODE
    otp = Challenge(ChallengeType.EMAIL_OTP, None, 0.8)
    assert resolver.resolve(otp, ChallengePolicy(), {"auth_test_integration"}) == ChallengeStrategy.AUTH_TEST_INTEGRATION


@pytest.mark.asyncio
async def test_registry_requires_verification_before_success():
    challenge = Challenge(ChallengeType.TOTP, None, 0.9)
    orchestrator = ChallengeOrchestrator()

    async def not_verified(_challenge):
        return False

    orchestrator.register(ChallengeStrategy.AUTH_TEST_INTEGRATION, not_verified)
    result = await orchestrator.handle(challenge, ChallengeStrategy.AUTH_TEST_INTEGRATION)
    assert result.status == ChallengeStatus.FAILED
    assert not result.verified


@pytest.mark.asyncio
async def test_human_fallback_is_not_reported_as_functional_failure():
    challenge = Challenge(ChallengeType.IMAGE_SELECTION, "recaptcha", 0.95)
    result = await ChallengeOrchestrator().handle(challenge, ChallengeStrategy.HUMAN_INTERVENTION)
    assert result.status == ChallengeStatus.MANUAL_INTERVENTION_REQUIRED
