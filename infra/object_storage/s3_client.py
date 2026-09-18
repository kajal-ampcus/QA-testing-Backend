"""
S3-compatible object storage client (S3 in prod, MinIO locally per
deploy/docker/docker-compose.yml) for large binary evidence — screenshots,
video, trace, HAR files. These never go in Postgres (architecture doc
Section 23); infra/db/models store only the storage key/URL.

Phase 0 stub.
"""

# TODO (Phase 1): class S3Client: async def put(key, data) -> str, async def get(key) -> bytes, ...
