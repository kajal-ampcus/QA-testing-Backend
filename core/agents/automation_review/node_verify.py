"""
Non-live Node checks for a generated suite.

Statuses are PASSED, FAILED, or NOT_VERIFIED. A check that did not run is
never reported as passed. `playwright test` is only invoked with `--list`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LIST_ARGS = ["playwright", "test", "--list"]


@dataclass
class VerificationResult:
    status: str
    tsc: str
    playwright_list: str
    detail: str = ""
    commands: list[list[str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "tsc": self.tsc,
            "playwright_list": self.playwright_list,
            "detail": self.detail,
        }


def not_verified(detail: str) -> VerificationResult:
    return VerificationResult(
        status="NOT_VERIFIED",
        tsc="NOT_VERIFIED",
        playwright_list="NOT_VERIFIED",
        detail=detail,
    )


def verify_suite(suite_dir: Path, *, runner: Any | None = None, timeout: int = 180) -> VerificationResult:
    node = shutil.which("node")
    npm = shutil.which("npm")
    npx = shutil.which("npx")
    if not node or not npm or not npx:
        return not_verified(
            "Node.js is not available in this runtime. "
            "tsc --noEmit and playwright test --list were not run."
        )
    execute = runner or subprocess.run
    env = {**os.environ, "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1"}
    install_cmd = [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"]
    tsc_cmd = [npx, "tsc", "--noEmit"]
    list_cmd = [npx, *LIST_ARGS]
    commands = [install_cmd, tsc_cmd, list_cmd]
    try:
        installed = execute(
            install_cmd,
            cwd=suite_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return VerificationResult(
            "FAILED",
            "NOT_VERIFIED",
            "NOT_VERIFIED",
            f"npm install did not finish: {type(exc).__name__}",
            commands,
        )
    if _code(installed) != 0:
        return VerificationResult(
            "FAILED",
            "NOT_VERIFIED",
            "NOT_VERIFIED",
            "npm install failed, so tsc and playwright test --list were not run.",
            commands,
        )
    tsc_status, tsc_detail = _run(execute, tsc_cmd, suite_dir, env, timeout)
    list_status, list_detail = _run(execute, list_cmd, suite_dir, env, timeout)
    if tsc_status == "PASSED" and list_status == "PASSED":
        overall = "PASSED"
    elif "NOT_VERIFIED" in {tsc_status, list_status} and "FAILED" not in {tsc_status, list_status}:
        overall = "NOT_VERIFIED"
    else:
        overall = "FAILED"
    detail = " ".join(part for part in (tsc_detail, list_detail) if part)
    return VerificationResult(overall, tsc_status, list_status, detail.strip(), commands)


def _run(execute: Any, command: list[str], suite_dir: Path, env: dict[str, str], timeout: int) -> tuple[str, str]:
    if command[-3:] != LIST_ARGS and command[-2:] != ["tsc", "--noEmit"]:
        return "NOT_VERIFIED", "Refused an unexpected verification command."
    try:
        completed = execute(
            command,
            cwd=suite_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "FAILED", f"{command[-1]} did not finish: {type(exc).__name__}"
    if _code(completed) == 0:
        return "PASSED", ""
    return "FAILED", f"{Path(command[0]).name} {' '.join(command[1:])} failed."


def _code(completed: Any) -> int:
    return int(getattr(completed, "returncode", 1))
