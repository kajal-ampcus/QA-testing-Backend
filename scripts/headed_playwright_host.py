"""Open a visible browser on this computer and run a generated Playwright suite.

The execution worker lives in Docker and has no desktop. It calls this process,
which runs `npx playwright test` here so you can watch the site being checked.
"""

from __future__ import annotations

import hmac
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_LOCK = threading.Lock()
_DEFAULT_HOST_ROOT = "D:/testing-platform/QA-testing-Backend/artifacts/automation"


def _host_root_from_dotenv() -> str:
    """Read AUTOMATION_HOST_ROOT from the backend .env without overriding the process env."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    try:
        from dotenv import dotenv_values
    except ImportError:
        return _host_root_from_env_file(env_path)
    value = dotenv_values(env_path).get("AUTOMATION_HOST_ROOT") or ""
    return value.strip().strip('"').strip("'")


def _host_root_from_env_file(env_path: Path) -> str:
    if not env_path.is_file():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key.strip() == "AUTOMATION_HOST_ROOT":
            return value.strip().strip('"').strip("'")
    return ""


def _allowed_root() -> Path:
    raw = os.environ.get("AUTOMATION_HOST_ROOT", "").strip()
    if not raw:
        raw = _host_root_from_dotenv()
    if not raw:
        raw = _DEFAULT_HOST_ROOT
    return Path(raw).resolve()


def _token() -> str:
    return os.environ.get("EXECUTION_HEADED_TOKEN", "local-dev-headed")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._send(404, {"detail": "Not found"})
            return
        self._send(200, {"ok": True})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/run":
            self._send(404, {"detail": "Not found"})
            return
        supplied = self.headers.get("X-Execution-Token", "")
        if not hmac.compare_digest(supplied, _token()):
            self._send(401, {"detail": "Unauthorized"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self._send(400, {"detail": "Expected JSON"})
            return
        if not isinstance(payload, dict):
            self._send(400, {"detail": "Expected an object"})
            return
        suite = _suite_dir(str(payload.get("suite_dir", "")))
        if suite is None:
            self._send(403, {"detail": "Suite folder is outside the automation artifacts directory"})
            return
        if not _LOCK.acquire(blocking=False):
            self._send(409, {"detail": "A browser run is already in progress"})
            return
        try:
            result = _run_suite(suite, payload)
        finally:
            _LOCK.release()
        self._send(200, result)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[headed-playwright] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _suite_dir(raw: str) -> Path | None:
    if not raw.strip():
        return None
    try:
        folder = Path(raw).resolve()
    except OSError:
        return None
    root = _allowed_root()
    if folder != root and root not in folder.parents:
        return None
    if not folder.is_dir():
        return None
    return folder


def _tool(name: str) -> str | None:
    """Resolve npm/npx, including the Windows .cmd shims."""
    if os.name == "nt":
        return shutil.which(f"{name}.cmd") or shutil.which(name)
    return shutil.which(name)


def _playwright_cli(suite: Path) -> Path:
    name = "playwright.cmd" if os.name == "nt" else "playwright"
    return suite / "node_modules" / ".bin" / name


def _execute(command: list[str], suite: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=suite,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _prepare_suite(suite: Path, env: dict[str, str], npm: str, npx: str) -> str | None:
    """Install the Windows Playwright CLI and Chromium when this PC cannot run the suite yet."""
    cli = _playwright_cli(suite)
    if not cli.is_file():
        modules = suite / "node_modules"
        if modules.is_dir():
            shutil.rmtree(modules)
        print(f"[headed-playwright] installing Playwright for {suite.name}", flush=True)
        try:
            installed = _execute(
                [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                suite,
                env,
                300,
            )
        except subprocess.TimeoutExpired:
            return "npm install timed out before Playwright could be installed on this computer."
        if installed.returncode != 0:
            detail = (installed.stderr or installed.stdout or "npm install failed").strip()
            return detail[-4000:]
        if not cli.is_file():
            return "npm install finished, but the Playwright command is still missing on this computer."
    print(f"[headed-playwright] ensuring Chromium is installed for {suite.name}", flush=True)
    try:
        browsers = _execute([npx, "playwright", "install", "chromium"], suite, env, 300)
    except subprocess.TimeoutExpired:
        return "Downloading Chromium timed out on this computer."
    if browsers.returncode != 0:
        detail = (browsers.stderr or browsers.stdout or "playwright install chromium failed").strip()
        return detail[-4000:]
    return None


def _run_suite(suite: Path, payload: dict) -> dict:
    npm = _tool("npm")
    npx = _tool("npx")
    if not npm or not npx:
        return {"returncode": 1, "stdout": "", "stderr": "npx is not available on this computer"}
    specs = payload.get("spec_paths") or []
    if not isinstance(specs, list):
        specs = []
    timeout = int(payload.get("timeout") or 900)
    env = os.environ.copy()
    env.pop("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", None)
    extra = payload.get("env") or {}
    if isinstance(extra, dict):
        for key in ("BASE_URL", "TEST_USERNAME", "TEST_PASSWORD", "RUN_DESTRUCTIVE"):
            value = extra.get(key)
            if isinstance(value, str) and value:
                env[key] = value
    prepare_error = _prepare_suite(suite, env, npm, npx)
    if prepare_error:
        return {"returncode": 1, "stdout": "", "stderr": prepare_error}
    command = [npx, "playwright", "test", "--config=playwright.execution.config.ts"]
    command.extend(str(item) for item in specs)
    print(f"[headed-playwright] opening a browser for {suite.name}", flush=True)
    try:
        completed = _execute(command, suite, env, timeout)
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 1,
            "stdout": str(exc.stdout or "")[-200_000:],
            "stderr": "Playwright timed out before the suite finished",
        }
    return {
        "returncode": int(completed.returncode),
        "stdout": (completed.stdout or "")[-200_000:],
        "stderr": (completed.stderr or "")[-200_000:],
    }


def main() -> None:
    host = os.environ.get("EXECUTION_HEADED_BIND", "0.0.0.0")
    port = int(os.environ.get("EXECUTION_HEADED_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[headed-playwright] listening on {host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
