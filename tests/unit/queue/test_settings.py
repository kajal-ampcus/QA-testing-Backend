"""Redis configuration must match between producer and worker processes."""

from unittest.mock import AsyncMock

from redis.exceptions import TimeoutError as RedisTimeoutError

from infra.queue import broker
from infra.queue.settings import QueueSettings
from scripts import check_redis


def test_dotenv_is_loaded_independently_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_CONNECT_TIMEOUT", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("REDIS_URL=redis://user:secret@queue.example:6380/3\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    settings = QueueSettings(_env_file=env_file).arq_settings()
    assert (settings.host, settings.port, settings.database) == ("queue.example", 6380, 3)
    assert settings.password == "secret"
    assert settings.conn_timeout == 5


def test_environment_overrides_dotenv_for_docker(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("REDIS_URL=redis://127.0.0.1:6379/0\n")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/2")
    monkeypatch.setenv("REDIS_CONNECT_TIMEOUT", "8")
    settings = QueueSettings(_env_file=env_file).arq_settings()
    assert settings.host == "redis"
    assert settings.database == 2
    assert settings.conn_timeout == 8


def test_default_uses_ipv4_loopback(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    settings = QueueSettings(_env_file=None).arq_settings()
    assert settings.host == "127.0.0.1"


async def test_broker_uses_shared_config(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://configured-host:6381/4")
    monkeypatch.setattr(broker, "_pool", None)
    create = AsyncMock()
    monkeypatch.setattr(broker, "create_pool", create)
    await broker.get_arq_pool()
    settings = create.call_args.args[0]
    assert (settings.host, settings.port, settings.database) == ("configured-host", 6381, 4)
    assert settings.conn_retries == 1


async def test_preflight_reports_failure_without_credentials(monkeypatch, capsys):
    monkeypatch.setenv("REDIS_URL", "redis://user:hidden-password@127.0.0.1:6379/0")
    monkeypatch.setattr(check_redis, "create_pool", AsyncMock(side_effect=RedisTimeoutError()))
    assert await check_redis.check() == 1
    output = capsys.readouterr().out
    assert "Redis unavailable" in output
    assert "hidden-password" not in output


async def test_preflight_closes_successful_connection(monkeypatch):
    pool = AsyncMock()
    monkeypatch.setattr(check_redis, "create_pool", AsyncMock(return_value=pool))
    assert await check_redis.check() == 0
    pool.aclose.assert_awaited_once()
