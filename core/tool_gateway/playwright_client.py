"""
Wraps the Playwright execution engine for the Test Execution Agent
(architecture doc Section 10/14) — the ONLY tool_gateway client used for
scripted test runs. Deliberately separate from mcp_clients/ (which are for
exploratory, agent-driven browser use) since Execution has zero LLM-in-the-
loop and should never share a code path with the exploratory tools.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from core.tool_gateway.secret_resolver import resolve_login

_OVERLAY_NAME = "playwright.execution.config.ts"
_REPORT_RELATIVE = "test-results/playwright-report.json"

# Headed Chromium on the worker virtual display. The Execution page streams it.
_LIVE_OVERLAY = """\
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

const noSandbox = process.env.CHROME_NO_SANDBOX === "true";
const executablePath = process.env.CHROME_EXECUTABLE_PATH || undefined;

export default defineConfig({
  ...base,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["json", { outputFile: "test-results/playwright-report.json" }],
    ["list"],
  ],
  use: {
    ...(base.use || {}),
    headless: false,
    viewport: { width: 1440, height: 900 },
    screenshot: "on",
    video: "on",
    trace: "on",
    launchOptions: {
      ...(executablePath ? { executablePath } : {}),
      args: noSandbox
        ? ["--no-sandbox", "--disable-dev-shm-usage"]
        : [],
    },
  },
});
"""

# Visible window on the machine that has a desktop. Keeps the suite's slowMo
# and maximized window from playwright.config.ts.
_HEADED_OVERLAY = """\
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
  ...base,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["json", { outputFile: "test-results/playwright-report.json" }],
    ["list"],
  ],
  use: {
    ...(base.use || {}),
    headless: false,
    screenshot: "on",
    video: "on",
    trace: "on",
  },
});
"""


@dataclass
class PlaywrightProcessResult:
    returncode: int
    report: dict[str, Any] | None
    stdout: str
    stderr: str
    report_path: Path
    commands: list[list[str]] = field(default_factory=list)


class PlaywrightClient:
    async def run(
        self,
        suite_dir: Path,
        *,
        spec_paths: list[str],
        project_id: UUID,
        base_url: str | None,
        credential_ref: str | None,
        run_destructive: bool,
        timeout: int = 900,
        runner: Any | None = None,
    ) -> PlaywrightProcessResult:
        env = await self._environment(
            project_id=project_id,
            base_url=base_url,
            credential_ref=credential_ref,
            run_destructive=run_destructive,
        )
        return await execute_suite(
            suite_dir,
            spec_paths=spec_paths,
            env=env,
            timeout=timeout,
            runner=runner,
        )

    async def _environment(
        self,
        *,
        project_id: UUID,
        base_url: str | None,
        credential_ref: str | None,
        run_destructive: bool,
    ) -> dict[str, str]:
        env = {
            **os.environ,
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
            "RUN_DESTRUCTIVE": "true" if run_destructive else "false",
        }
        if base_url:
            env["BASE_URL"] = base_url
        if not credential_ref:
            return env
        secret = await resolve_login(credential_ref, project_id)
        env["TEST_USERNAME"] = secret["username"]
        env["TEST_PASSWORD"] = secret["password"]
        return env


async def execute_suite(
    suite_dir: Path,
    *,
    spec_paths: list[str],
    env: dict[str, str],
    timeout: int = 900,
    runner: Any | None = None,
) -> PlaywrightProcessResult:
    node = shutil.which("node")
    npm = shutil.which("npm")
    npx = shutil.which("npx")
    if not node or not npm or not npx:
        raise RuntimeError("Node.js is not available in this runtime, so Playwright cannot run.")
    suite_dir.mkdir(parents=True, exist_ok=True)
    headed_host = os.environ.get("EXECUTION_HEADED_HOST", "").strip()
    overlay = suite_dir / _OVERLAY_NAME
    overlay.write_text(_HEADED_OVERLAY if headed_host else _LIVE_OVERLAY, encoding="utf-8")
    (suite_dir / "test-results").mkdir(exist_ok=True)
    if headed_host:
        return await _run_on_headed_host(
            suite_dir,
            spec_paths=spec_paths,
            env=env,
            timeout=timeout,
            host_url=headed_host,
        )

    execute = runner or subprocess.run
    commands: list[list[str]] = []
    if not (suite_dir / "node_modules").is_dir():
        install = [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"]
        commands.append(install)
        installed = await asyncio.to_thread(
            execute,
            install,
            cwd=suite_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=min(timeout, 180),
            check=False,
        )
        if int(getattr(installed, "returncode", 1)) != 0:
            return PlaywrightProcessResult(
                returncode=int(getattr(installed, "returncode", 1)),
                report=None,
                stdout=str(getattr(installed, "stdout", "")),
                stderr=str(getattr(installed, "stderr", "") or "npm install failed"),
                report_path=suite_dir / _REPORT_RELATIVE,
                commands=commands,
            )

    test_cmd = [npx, "playwright", "test", f"--config={_OVERLAY_NAME}"]
    test_cmd.extend(spec_paths)
    commands.append(test_cmd)
    completed = await asyncio.to_thread(
        execute,
        test_cmd,
        cwd=suite_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    report_path = suite_dir / _REPORT_RELATIVE
    report = _read_report(report_path)
    return PlaywrightProcessResult(
        returncode=int(getattr(completed, "returncode", 1)),
        report=report,
        stdout=str(getattr(completed, "stdout", "")),
        stderr=str(getattr(completed, "stderr", "")),
        report_path=report_path,
        commands=commands,
    )


def host_suite_dir(suite_dir: Path) -> str:
    """Map the container suite folder to the folder on the desktop machine."""
    host_root = os.environ.get("AUTOMATION_HOST_ROOT", "").strip().replace("\\", "/").rstrip("/")
    artifact_root = os.environ.get("AUTOMATION_ARTIFACT_DIR", "/app/artifacts/automation").strip().replace("\\", "/").rstrip("/")
    raw = str(suite_dir).replace("\\", "/").rstrip("/")
    if host_root and artifact_root and (raw == artifact_root or raw.startswith(artifact_root + "/")):
        return f"{host_root}{raw[len(artifact_root):]}"
    return raw


async def _run_on_headed_host(
    suite_dir: Path,
    *,
    spec_paths: list[str],
    env: dict[str, str],
    timeout: int,
    host_url: str,
) -> PlaywrightProcessResult:
    """Ask the desktop process to run npx playwright test with a visible browser."""
    forwarded = {
        key: env[key]
        for key in ("BASE_URL", "TEST_USERNAME", "TEST_PASSWORD", "RUN_DESTRUCTIVE")
        if env.get(key)
    }
    payload = {
        "suite_dir": host_suite_dir(suite_dir),
        "spec_paths": spec_paths,
        "timeout": timeout,
        "env": forwarded,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{host_url.rstrip('/')}/v1/run",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Execution-Token": os.environ.get("EXECUTION_HEADED_TOKEN", ""),
        },
        method="POST",
    )

    def _post() -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=timeout + 60) as response:
                loaded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"The visible browser runner refused the suite ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "The visible browser runner is not available on this computer, so Playwright cannot open a window."
            ) from exc
        if not isinstance(loaded, dict):
            raise RuntimeError("The visible browser runner returned an unexpected response.")
        return loaded

    try:
        loaded = await asyncio.to_thread(_post)
    except RuntimeError as exc:
        return PlaywrightProcessResult(
            returncode=1,
            report=None,
            stdout="",
            stderr=str(exc),
            report_path=suite_dir / _REPORT_RELATIVE,
            commands=[["headed-host", host_suite_dir(suite_dir)]],
        )
    report_path = suite_dir / _REPORT_RELATIVE
    return PlaywrightProcessResult(
        returncode=int(loaded.get("returncode", 1)),
        report=_read_report(report_path),
        stdout=str(loaded.get("stdout", "")),
        stderr=str(loaded.get("stderr", "")),
        report_path=report_path,
        commands=[["headed-host", host_suite_dir(suite_dir)]],
    )


def _read_report(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None
