import pytest

from infra.object_storage.s3_client import InMemoryS3Client, evidence_key


@pytest.mark.asyncio
async def test_in_memory_s3_put_and_get_roundtrip() -> None:
    client = InMemoryS3Client()
    key = await client.put_evidence(
        project_id="project",
        run_id="run",
        result_id="result",
        channel="screenshot",
        data=b"png-bytes",
    )
    assert key == evidence_key("project", "run", "result", "screenshot")
    assert await client.get(key) == b"png-bytes"


@pytest.mark.asyncio
async def test_in_memory_s3_missing_key_raises() -> None:
    client = InMemoryS3Client()
    with pytest.raises(KeyError):
        await client.get("missing")
