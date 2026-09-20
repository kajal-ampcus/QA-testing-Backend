"""Container configuration must preserve secrets and support unattended browser startup."""

import os

from sqlalchemy.engine import make_url

from core.tool_gateway.mcp_clients.chrome_devtools_client import ChromeDevToolsClient
from scripts.docker_entrypoint import configure_database


def test_docker_database_url_escapes_password(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://old:old@localhost/old")
    monkeypatch.setenv("DB_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_USER", "qa_platform")
    monkeypatch.setenv("POSTGRES_PASSWORD", "a@b:/?#%password")
    monkeypatch.setenv("POSTGRES_DB", "qa_platform")
    configure_database()
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host == "postgres"
    assert url.password == "a@b:/?#%password"
    assert url.drivername == "postgresql+asyncpg"


def test_non_docker_database_url_is_preserved(monkeypatch):
    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://local:local@localhost/local")
    configure_database()
    assert os.environ["DATABASE_URL"] == "postgresql+asyncpg://local:local@localhost/local"


def test_worker_uses_installed_mcp_and_explicit_browser(monkeypatch):
    monkeypatch.setenv("CHROME_DEVTOOLS_MCP_COMMAND", "chrome-devtools-mcp")
    monkeypatch.setenv("CHROME_EXECUTABLE_PATH", "/usr/bin/google-chrome")
    monkeypatch.setenv("CHROME_NO_SANDBOX", "true")
    params = ChromeDevToolsClient()._server_params()
    assert params.command == "chrome-devtools-mcp"
    assert "chrome-devtools-mcp@latest" not in params.args
    assert "--executablePath" in params.args
    assert "/usr/bin/google-chrome" in params.args
    assert "--chromeArg=--no-sandbox" in params.args


def test_local_mcp_install_is_noninteractive_without_disabling_sandbox(monkeypatch):
    for key in ("CHROME_DEVTOOLS_MCP_COMMAND", "CHROME_EXECUTABLE_PATH", "CHROME_NO_SANDBOX"):
        monkeypatch.delenv(key, raising=False)
    params = ChromeDevToolsClient()._server_params()
    assert params.command in {"npx", "npx.cmd"}
    assert params.args[:2] == ["-y", "chrome-devtools-mcp@latest"]
    assert "--chromeArg=--no-sandbox" not in params.args
