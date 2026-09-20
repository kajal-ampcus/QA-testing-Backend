"""Shared Redis configuration for API producers and arq workers."""

from pathlib import Path

from arq.connections import RedisSettings
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class QueueSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_connect_timeout: float = Field(default=5.0, gt=0)

    def arq_settings(self) -> RedisSettings:
        settings = RedisSettings.from_dsn(self.redis_url)
        settings.conn_timeout = self.redis_connect_timeout
        return settings
