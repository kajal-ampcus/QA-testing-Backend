"""
Resolves an opaque credential_ref (e.g. "cred:qa_admin") to the actual secret,
ONLY at the point of use (e.g. submitting a login form). This is the entire
mechanism behind "credentials never enter an agent's context window"
(architecture doc Section 20/28) — no agent, log, or LLM prompt ever sees a
literal password.

Phase 0 stub.
"""

# TODO (Phase 1): async def resolve(credential_ref: str) -> str: ...
#   delegates to infra/secrets/vault_client.py
