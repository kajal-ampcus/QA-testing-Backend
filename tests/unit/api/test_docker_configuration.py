"""Container configuration must preserve secrets and support unattended browser startup."""

import os
from pathlib import Path

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
    monkeypatch.setenv("CHROME_EXECUTABLE_PATH", "/usr/local/bin/google-chrome")
    monkeypatch.setenv("CHROME_NO_SANDBOX", "true")
    params = ChromeDevToolsClient()._server_params()
    assert params.command == "chrome-devtools-mcp"
    assert "chrome-devtools-mcp@latest" not in params.args
    assert "--executablePath" in params.args
    assert "/usr/local/bin/google-chrome" in params.args
    assert "--chromeArg=--no-sandbox" in params.args
    assert "--chromeArg=--disable-dev-shm-usage" in params.args
    assert params.env is not None
    assert "PATH" in params.env


def test_local_mcp_install_is_noninteractive_without_disabling_sandbox(monkeypatch):
    for key in ("CHROME_DEVTOOLS_MCP_COMMAND", "CHROME_EXECUTABLE_PATH", "CHROME_NO_SANDBOX"):
        monkeypatch.delenv(key, raising=False)
    params = ChromeDevToolsClient()._server_params()
    if os.name == "nt":
        assert params.command == "cmd"
        assert params.args[:4] == ["/c", "npx", "-y", "chrome-devtools-mcp@latest"]
    else:
        assert params.command == "npx"
        assert params.args[:2] == ["-y", "chrome-devtools-mcp@latest"]
    assert "--chromeArg=--no-sandbox" not in params.args


def test_windows_npx_goes_through_cmd():
    command, args = ChromeDevToolsClient._npx_command(["--headless"], windows=True)
    assert command == "cmd"
    assert args[:4] == ["/c", "npx", "-y", "chrome-devtools-mcp@latest"]


def test_url_allowlist_keeps_blank_and_chrome_pages(monkeypatch):
    for key in ("CHROME_DEVTOOLS_MCP_COMMAND", "CHROME_EXECUTABLE_PATH", "CHROME_NO_SANDBOX"):
        monkeypatch.delenv(key, raising=False)
    params = ChromeDevToolsClient(allowed_url_pattern="https://sample.test/app")._server_params()
    patterns = [
        params.args[i + 1]
        for i, arg in enumerate(params.args)
        if arg == "--allowedUrlPattern"
    ]
    assert "https://sample.test/*" in patterns
    assert "about:*" in patterns
    assert "chrome://*" in patterns


def test_compose_shares_discovery_evidence_between_api_and_worker():
    compose = Path(__file__).resolve().parents[3] / "compose.yaml"
    text = compose.read_text(encoding="utf-8")
    evidence = "./artifacts/discovery:/app/artifacts/discovery"
    assert "DISCOVERY_EVIDENCE_DIR: /app/artifacts/discovery" in text
    assert text.count(evidence) >= 2
    assert "host.docker.internal:host-gateway" in text
    assert "VITE_API_KEY: ${VITE_API_KEY:-${API_KEY:-}}" in text
    frontend_dockerfile = (
        Path(__file__).resolve().parents[4] / "QA-testing-Frontend" / "Dockerfile"
    )
    frontend = frontend_dockerfile.read_text(encoding="utf-8")
    assert "ARG VITE_API_KEY=" in frontend
    nginx = (frontend_dockerfile.parent / "nginx.conf").read_text(encoding="utf-8")
    assert "X-API-Key" in nginx
    assert "Authorization" in nginx
    api_dockerfile = Path(__file__).resolve().parents[3] / "deploy" / "docker" / "Dockerfile.api"
    assert "/app/artifacts/discovery" in api_dockerfile.read_text(encoding="utf-8")
    assert "/app/artifacts/automation" in api_dockerfile.read_text(encoding="utf-8")
    assert "node:22-bookworm-slim" in api_dockerfile.read_text(encoding="utf-8")
    worker_dockerfile = Path(__file__).resolve().parents[3] / "deploy" / "docker" / "Dockerfile.worker"
    assert "/app/artifacts/discovery" in worker_dockerfile.read_text(encoding="utf-8")
    assert "/app/artifacts/automation" in worker_dockerfile.read_text(encoding="utf-8")
    automation = "./artifacts/automation:/app/artifacts/automation"
    assert "AUTOMATION_ARTIFACT_DIR: /app/artifacts/automation" in text
    assert "AUTOMATION_HOST_ROOT:" in text
    assert 'DISPLAY: ":99"' in text
    assert "EXECUTION_HEADED_HOST" not in text
    assert text.count(automation) >= 2
    assert "xvfb" in worker_dockerfile.read_text(encoding="utf-8")
    assert "x11vnc" in worker_dockerfile.read_text(encoding="utf-8")
    assert "novnc" in worker_dockerfile.read_text(encoding="utf-8")
    assert "websockify" in worker_dockerfile.read_text(encoding="utf-8")
    assert "run_worker.py" in worker_dockerfile.read_text(encoding="utf-8")
    assert "location /live/" in nginx
