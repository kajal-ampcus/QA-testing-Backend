"""
Shared base repository — common CRUD/versioning helpers (append-only version
writes per Section 23) that concrete repositories extend. Deliberately NOT a
generic "CRUD everything" base per docs/PROJECT_STRUCTURE.md point 8 — this
only holds what's genuinely common (versioning, tenant scoping), each
aggregate root still gets its own repository with its own query surface.

Phase 0 stub.
"""
