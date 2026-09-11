"""
Background worker entrypoint. Same core/ as apps/api — this process actually
runs Discovery crawls and Execution runs, which can take minutes and don't
belong inside an HTTP request/response cycle (docs/PROJECT_STRUCTURE.md point 2).

Phase 0 stub — choose Celery+Redis or arq before Phase 1 (see
docs/PROJECT_STRUCTURE.md, "A few concrete choices to lock in") and replace
this file accordingly. Keeping both approaches out of the codebase at once
is the point of flagging this rather than guessing.
"""

# Celery option:
# from celery import Celery
# celery_app = Celery("qa_platform", broker=settings.REDIS_URL, include=["apps.worker.tasks"])

# arq option:
# from arq.connections import RedisSettings
# class WorkerSettings:
#     functions = [...]  # import task functions from apps.worker.tasks
#     redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
