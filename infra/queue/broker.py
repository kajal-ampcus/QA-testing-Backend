"""
Task broker abstraction the orchestrator uses to dispatch work to
apps/worker/tasks/* (architecture doc Section 25/33). Backing implementation
depends on the worker/queue library choice (Celery+Redis vs. arq) — see
docs/PROJECT_STRUCTURE.md, still an open decision.

Phase 0 stub.
"""

# TODO (Phase 1): async def enqueue(task_name: str, payload: dict) -> str: ...
