"""
Injects project_id/tenant scope on every request (architecture doc Section 28:
Security — multi-tenancy). This is what stops an agent or a route handler from
ever being able to construct a cross-tenant query — the scope is applied here,
once, not re-checked ad hoc in every route.

Phase 0 stub.
"""
