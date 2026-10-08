"""
Resolves an opaque credential_ref (e.g. "cred:qa_admin") to the actual secret,
ONLY at the point of use (e.g. submitting a login form). This is the entire
mechanism behind "credentials never enter an agent's context window"
(architecture doc Section 20/28) — no agent, log, or LLM prompt ever sees a
literal password.
"""

import uuid
from typing import Any

from infra.secrets.vault_client import get_login_secret


async def resolve_login(
    credential_ref: str, project_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Resolve a credential reference only at the browser-tool boundary."""
    return await get_login_secret(credential_ref, project_id)


async def resolve_login_url(
    credential_ref: str | None, project_id: uuid.UUID, default: str
) -> str:
    """Return the non-secret login URL stored with a credential, or `default`."""
    if not credential_ref:
        return default
    try:
        secret = await get_login_secret(credential_ref, project_id)
    except RuntimeError:
        return default
    return secret.get("login_url", default)
