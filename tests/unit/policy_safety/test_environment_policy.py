"""Discovery targets cannot point the worker's browser at internal infrastructure."""

import pytest

from core.policy_safety import environment_policy
from core.policy_safety.environment_policy import (
    DiscoveryTargetError,
    validate_discovery_target,
)


@pytest.fixture(autouse=True)
def isolate_localhost_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("DISCOVERY_ALLOW_LOCALHOST", raising=False)
    monkeypatch.setattr(environment_policy, "_ENV_FILE", tmp_path / ".env")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_localhost_dotenv_toggle(host, enabled, monkeypatch):
    monkeypatch.setenv("DISCOVERY_ALLOW_PRIVATE_TARGETS", "false" if enabled else "true")
    environment_policy._ENV_FILE.write_text(f"DISCOVERY_ALLOW_LOCALHOST={str(enabled).lower()}\n")
    if enabled:
        await validate_discovery_target(f"http://{host}:3000/")
    else:
        with pytest.raises(DiscoveryTargetError, match="Localhost discovery targets are disabled"):
            await validate_discovery_target(f"http://{host}:3000/")


async def test_localhost_environment_overrides_dotenv(monkeypatch):
    environment_policy._ENV_FILE.write_text("DISCOVERY_ALLOW_LOCALHOST=true\n")
    monkeypatch.setenv("DISCOVERY_ALLOW_LOCALHOST", "false")
    with pytest.raises(DiscoveryTargetError, match="Localhost discovery targets are disabled"):
        await validate_discovery_target("http://127.0.0.1:3000/")


@pytest.mark.parametrize("host", ["10.0.0.5", "169.254.169.254", "metadata.google.internal"])
async def test_localhost_flag_does_not_allow_other_restricted_targets(host, monkeypatch):
    monkeypatch.setenv("DISCOVERY_ALLOW_LOCALHOST", "true")
    monkeypatch.setenv("DISCOVERY_ALLOW_PRIVATE_TARGETS", "false")
    with pytest.raises(DiscoveryTargetError):
        await validate_discovery_target(f"http://{host}/")


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


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]"])
async def test_private_targets_allowed_in_development(monkeypatch, host) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("DISCOVERY_ALLOW_PRIVATE_TARGETS", raising=False)
    await validate_discovery_target(f"http://{host}:3000/")


@pytest.mark.parametrize("host", ["10.0.0.5", "127.0.0.1", "[::1]"])
async def test_private_targets_refused_outside_development(monkeypatch, host) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("DISCOVERY_ALLOW_PRIVATE_TARGETS", raising=False)
    with pytest.raises(DiscoveryTargetError):
        await validate_discovery_target(f"http://{host}/")


async def test_allowlist_restricts_hosts(monkeypatch) -> None:
    monkeypatch.setenv("DISCOVERY_ALLOWED_HOSTS", "*.example.test")
    with pytest.raises(DiscoveryTargetError):
        await validate_discovery_target("http://127.0.0.1/")
