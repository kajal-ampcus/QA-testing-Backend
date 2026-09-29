import asyncio

from arq.jobs import JobStatus

from apps.api.routers.v1 import application_maps


def test_cancel_timeout_is_reported_as_pending_instead_of_500(monkeypatch) -> None:
    class SlowAbortJob:
        def __init__(self, job_id, pool) -> None:
            self.job_id = job_id

        async def status(self):
            return JobStatus.in_progress

        async def abort(self, timeout):
            assert timeout == 5
            raise TimeoutError

    async def pool():
        return object()

    monkeypatch.setattr(application_maps, "Job", SlowAbortJob)
    monkeypatch.setattr(application_maps, "get_arq_pool", pool)

    result = asyncio.run(application_maps.cancel_discovery_job("slow-job"))

    assert result.job_id == "slow-job"
    assert result.status == "cancellation_requested"


def test_cancel_reports_success_when_worker_acknowledges(monkeypatch) -> None:
    class CancelledJob:
        def __init__(self, job_id, pool) -> None:
            self.job_id = job_id

        async def status(self):
            return JobStatus.in_progress

        async def abort(self, timeout):
            return True

    async def pool():
        return object()

    monkeypatch.setattr(application_maps, "Job", CancelledJob)
    monkeypatch.setattr(application_maps, "get_arq_pool", pool)

    result = asyncio.run(application_maps.cancel_discovery_job("running-job"))

    assert result.status == "cancelled"
