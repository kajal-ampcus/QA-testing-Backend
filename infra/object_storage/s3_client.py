"""
S3-compatible object storage client (S3 in prod, MinIO locally per
deploy/docker/docker-compose.yml) for large binary evidence — screenshots,
video, trace, HAR files. These never go in Postgres (architecture doc
Section 23); infra/db/models store only the storage key/URL.
"""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from infra.object_storage.settings import ObjectStorageSettings

_CONTENT_TYPES = {
    "screenshot": "image/png",
    "video": "video/webm",
    "trace": "application/zip",
    "console_log": "application/json",
    "network_log": "application/json",
}


def evidence_key(
    project_id: str, run_id: str, result_id: str, channel: str, suffix: str = ""
) -> str:
    name = f"{channel}{suffix}"
    return f"executions/{project_id}/{run_id}/{result_id}/{name}"


class S3Client:
    def __init__(
        self,
        settings: ObjectStorageSettings | None = None,
        client: BaseClient | None = None,
    ) -> None:
        self.settings = settings or ObjectStorageSettings()
        self._client = client or _boto_client(self.settings)
        self.bucket = self.settings.object_storage_bucket

    async def put(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> str:
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            **extra,
        )
        return key

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(
            self._client.get_object, Bucket=self.bucket, Key=key
        )
        body = response["Body"]
        return await asyncio.to_thread(body.read)

    async def put_evidence(
        self,
        *,
        project_id: str,
        run_id: str,
        result_id: str,
        channel: str,
        data: bytes,
        suffix: str = "",
    ) -> str:
        key = evidence_key(project_id, run_id, result_id, channel, suffix)
        return await self.put(key, data, _CONTENT_TYPES.get(channel))


class InMemoryS3Client:
    """Test double. Same put/get surface as S3Client, no network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> str:
        self.objects[key] = data
        return key

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    async def put_evidence(
        self,
        *,
        project_id: str,
        run_id: str,
        result_id: str,
        channel: str,
        data: bytes,
        suffix: str = "",
    ) -> str:
        key = evidence_key(project_id, run_id, result_id, channel, suffix)
        return await self.put(key, data, _CONTENT_TYPES.get(channel))


def _boto_client(settings: ObjectStorageSettings) -> BaseClient:
    return boto3.client(
        "s3",
        endpoint_url=settings.object_storage_endpoint or None,
        aws_access_key_id=settings.object_storage_access_key,
        aws_secret_access_key=settings.object_storage_secret_key,
        region_name=settings.object_storage_region,
        config=Config(s3={"addressing_style": "path"}),
    )
