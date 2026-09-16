"""
Resolves an opaque credential_ref (e.g. "cred:qa_admin") to the actual secret,
ONLY at the point of use (e.g. submitting a login form). This is the entire
mechanism behind "credentials never enter an agent's context window"
(architecture doc Section 20/28) — no agent, log, or LLM prompt ever sees a
literal password.

Phase 0 stub.
"""

from infra.secrets.vault_client import get_login_secret


async def resolve_login(credential_ref: str) -> dict[str, str]:
    """Resolve a credential reference only at the browser-tool boundary."""
    return await get_login_secret(credential_ref)
