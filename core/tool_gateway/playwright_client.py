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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from core.tool_gateway.secret_resolver import resolve_login

_OVERLAY_NAME = "playwright.execution.config.ts"
_REPORT_RELATIVE = "test-results/playwright-report.json"
_OVERLAY = """\
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

const noSandbox = process.env.CHROME_NO_SANDBOX === "true";
const executablePath = process.env.CHROME_EXECUTABLE_PATH || undefined;

export default defineConfig({
  ...base,
  fullyParallel: false,
  retries: 0,
  reporter: [
    ["json", { outputFile: "test-results/playwright-report.json" }],
    ["list"],
  ],
  use: {
    ...(base.use || {}),
    screenshot: "on",
    video: "on",
    trace: "on",
    launchOptions: {
      ...(executablePath ? { executablePath } : {}),
      args: noSandbox ? ["--no-sandbox", "--disable-dev-shm-usage"] : [],
    },
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
    overlay = suite_dir / _OVERLAY_NAME
    overlay.write_text(_OVERLAY, encoding="utf-8")
    (suite_dir / "test-results").mkdir(exist_ok=True)

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


def _read_report(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None
