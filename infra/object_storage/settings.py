"""MinIO/S3 settings for execution evidence. Binaries never go in Postgres."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class ObjectStorageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    object_storage_endpoint: str = "http://127.0.0.1:9000"
    object_storage_bucket: str = "qa-platform-evidence"
    object_storage_access_key: str = "minioadmin"
    object_storage_secret_key: str = "minioadmin"
    object_storage_region: str = "us-east-1"
