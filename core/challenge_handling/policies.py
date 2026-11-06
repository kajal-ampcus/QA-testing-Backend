"""Configuration and production safety checks for authorized strategies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChallengePolicy:
    enabled: bool = True
    auto_detect: bool = True
    allow_staging_test_hooks: bool = True
    allow_provider_test_modes: bool = True
    allow_auth_test_integrations: bool = True
    human_fallback: bool = True
    max_retries: int = 2
    timeout_seconds: int = 60
    environment: str = "test"

    @property
    def is_production(self) -> bool:
        return self.environment.strip().casefold() in {"prod", "production"}

    def staging_hooks_allowed(self) -> bool:
        # Fail closed: a staging hook may never be selected in production.
        return self.allow_staging_test_hooks and not self.is_production
