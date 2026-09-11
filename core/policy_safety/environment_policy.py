"""
Environment-type gating (architecture doc Section 28/29): extra approval
gates automatically apply whenever environment.type == production. Also
governs which safety checks are active per environment (e.g. destructive
actions may be pre-approved in a deliberately configured sandbox, never in
production).

Phase 0 stub.
"""

# TODO (Phase 1): def policy_for_environment(environment) -> EnvironmentPolicy: ...
