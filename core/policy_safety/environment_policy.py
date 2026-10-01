"""
Environment-type gating (architecture doc Section 28/29): extra approval
gates automatically apply whenever environment.type == production. Also
governs which safety checks are active per environment.

Discovery target policy: the worker's browser follows whatever URL a user
submits, so the target is checked before a crawl is queued. Cloud metadata,
link-local, multicast and reserved addresses are always refused. Loopback and
private networks are allowed in development (local apps under test) and
refused elsewhere unless DISCOVERY_ALLOW_PRIVATE_TARGETS=true.
DISCOVERY_ALLOWED_HOSTS (comma-separated, "*.example.com" wildcards) restricts
targets to an explicit allowlist when set.
"""

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlparse

_METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}


class DiscoveryTargetError(ValueError):
    """The requested discovery URL is not an allowed crawl target."""


def _environment() -> str:
    return os.environ.get("ENVIRONMENT", "development").strip().lower()


def _allow_private() -> bool:
    override = os.environ.get("DISCOVERY_ALLOW_PRIVATE_TARGETS")
    if override is not None:
        return override.strip().lower() == "true"
    return _environment() == "development"


def _allowed_hosts() -> list[str]:
    raw = os.environ.get("DISCOVERY_ALLOWED_HOSTS", "")
    return [host.strip().lower() for host in raw.split(",") if host.strip()]


def _host_matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) or host == pattern[2:]
    return host == pattern


def check_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if (
        address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        raise DiscoveryTargetError(f"Discovery target address {address} is not allowed")
    if (address.is_loopback or address.is_private) and not _allow_private():
        raise DiscoveryTargetError(
            f"Discovery target resolves to a private address ({address}). "
            "Set DISCOVERY_ALLOW_PRIVATE_TARGETS=true to crawl internal applications."
        )


async def validate_discovery_target(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DiscoveryTargetError("Discovery target must be an http(s) URL with a hostname")
    host = parsed.hostname.lower().rstrip(".")
    if host in _METADATA_HOSTS:
        raise DiscoveryTargetError("Cloud metadata endpoints cannot be crawled")
    allowlist = _allowed_hosts()
    if allowlist and not any(_host_matches(host, pattern) for pattern in allowlist):
        raise DiscoveryTargetError(f"Host {host} is not in DISCOVERY_ALLOWED_HOSTS")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        check_address(literal)
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise DiscoveryTargetError(f"Discovery target host {host} cannot be resolved") from exc
    for info in infos:
        check_address(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
