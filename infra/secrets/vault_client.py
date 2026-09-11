"""
Vault (or cloud KMS) client — the actual secret store backing
core/tool_gateway/secret_resolver.py. This module is the ONLY thing that ever
touches a real secret value; everything upstream of the Tool Gateway only
ever sees a credential_ref (architecture doc Section 20/28).

Phase 0 stub.
"""

# TODO (Phase 1): async def get_secret(ref: str) -> str: ...
