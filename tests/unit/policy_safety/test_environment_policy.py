"""Discovery targets cannot point the worker's browser at internal infrastructure."""

import pytest

from core.policy_safety.environment_policy import (
    DiscoveryTargetError,
    validate_discovery_target,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[fe80::1]/",
        "http://0.0.0.0/",
    ],
)
async def test_metadata_and_link_local_targets_are_always_refused(url, monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    with pytest.raises(DiscoveryTargetError):
        await validate_discovery_target(url)


async def test_private_targets_allowed_in_development(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("DISCOVERY_ALLOW_PRIVATE_TARGETS", raising=False)
    await validate_discovery_target("http://127.0.0.1:3000/")


async def test_private_targets_refused_outside_development(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("DISCOVERY_ALLOW_PRIVATE_TARGETS", raising=False)
    with pytest.raises(DiscoveryTargetError):
        await validate_discovery_target("http://10.0.0.5/")


async def test_allowlist_restricts_hosts(monkeypatch) -> None:
    monkeypatch.setenv("DISCOVERY_ALLOWED_HOSTS", "*.example.test")
    with pytest.raises(DiscoveryTargetError):
        await validate_discovery_target("http://127.0.0.1/")
