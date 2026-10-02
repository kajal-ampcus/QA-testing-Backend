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


def _allowed_root() -> Path:
    raw = os.environ.get(
        "AUTOMATION_HOST_ROOT",
        "D:/testing-platform/QA-testing-Backend/artifacts/automation",
    )
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


def _run_suite(suite: Path, payload: dict) -> dict:
    npx = shutil.which("npx")
    if not npx:
        return {"returncode": 1, "stdout": "", "stderr": "npx is not available on this computer"}
    specs = payload.get("spec_paths") or []
    if not isinstance(specs, list):
        specs = []
    timeout = int(payload.get("timeout") or 900)
    env = os.environ.copy()
    extra = payload.get("env") or {}
    if isinstance(extra, dict):
        for key in ("BASE_URL", "TEST_USERNAME", "TEST_PASSWORD", "RUN_DESTRUCTIVE"):
            value = extra.get(key)
            if isinstance(value, str) and value:
                env[key] = value
    command = [npx, "playwright", "test", "--config=playwright.execution.config.ts"]
    command.extend(str(item) for item in specs)
    print(f"[headed-playwright] opening a browser for {suite.name}", flush=True)
    try:
        completed = subprocess.run(
            command,
            cwd=suite,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
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
