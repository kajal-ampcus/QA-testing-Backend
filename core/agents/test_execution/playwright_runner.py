"""
Thin wrapper around core/tool_gateway/playwright_client.py for running a
compiled test script and capturing the five evidence channels concurrently.
Deterministic infrastructure — no LLM calls, no judgment calls. This is the
one place in the whole system where a test's pass/fail status is actually
decided (by assertion result), matching architecture doc Section 14.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from core.agents.test_execution.eligibility import ScriptSnapshot
from core.agents.test_execution.live_display import append_live_log
from core.tool_gateway.playwright_client import PlaywrightClient, PlaywrightProcessResult
from domain.enums import TestResultStatus

_EXPECT = re.compile(
    r"Expected[:\s]+(?P<expected>.+?)(?:\r?\n)Received[:\s]+(?P<actual>.+?)(?:\r?\n|$)",
    re.IGNORECASE | re.DOTALL,
)
_STATUS = {
    "passed": TestResultStatus.PASSED,
    "expected": TestResultStatus.PASSED,
    "failed": TestResultStatus.FAILED,
    "unexpected": TestResultStatus.FAILED,
    "timedout": TestResultStatus.FAILED,
    "timedOut": TestResultStatus.FAILED,
    "interrupted": TestResultStatus.ERROR,
    "skipped": TestResultStatus.SKIPPED,
}


@dataclass
class RawTestOutcome:
    spec_path: str
    title: str
    status: TestResultStatus
    duration_ms: int | None
    error_message: str | None
    expected: str
    actual: str
    screenshot: bytes | None = None
    video: bytes | None = None
    trace: bytes | None = None
    console_log: bytes | None = None
    network_log: bytes | None = None
    script_id: UUID | None = None


@dataclass
class SuiteRunResult:
    process_ok: bool
    detail: str
    outcomes: list[RawTestOutcome] = field(default_factory=list)
    raw: PlaywrightProcessResult | None = None


class PlaywrightRunner:
    def __init__(self, client: PlaywrightClient | None = None) -> None:
        self.client = client or PlaywrightClient()

    async def run_suite(
        self,
        suite_dir: Path,
        *,
        scripts: list[ScriptSnapshot],
        project_id: UUID,
        base_url: str | None,
        credential_ref: str | None,
        run_destructive: bool,
        timeout: int = 900,
        runner: Any | None = None,
        run_id: UUID | None = None,
        on_outcome: Callable[[RawTestOutcome], Awaitable[None]] | None = None,
    ) -> SuiteRunResult:
        spec_paths = [script.file_path for script in scripts]

        async def on_output(text: str) -> None:
            if run_id is not None:
                await append_live_log(str(run_id), text)

        async def on_case(payload: dict[str, Any]) -> None:
            if on_outcome is None:
                return
            outcome = outcome_from_progress(payload, suite_dir, scripts)
            if outcome is not None:
                await on_outcome(outcome)

        process = await self.client.run(
            suite_dir,
            spec_paths=spec_paths,
            project_id=project_id,
            base_url=base_url,
            credential_ref=credential_ref,
            run_destructive=run_destructive,
            timeout=timeout,
            runner=runner,
            on_output=on_output if run_id is not None else None,
            on_case=on_case if on_outcome is not None else None,
        )
        if process.report is None:
            return SuiteRunResult(
                process_ok=False,
                detail=process.stderr.strip() or process.stdout.strip() or "Playwright produced no JSON report.",
                outcomes=[],
                raw=process,
            )
        outcomes = parse_playwright_json(process.report, suite_dir, scripts)
        return SuiteRunResult(
            process_ok=True,
            detail="",
            outcomes=outcomes,
            raw=process,
        )


def outcome_from_progress(
    payload: dict[str, Any],
    suite_dir: Path,
    scripts: list[ScriptSnapshot],
) -> RawTestOutcome | None:
    """One finished test, recorded before the suite process exits."""
    spec_path = _relative(str(payload.get("file") or ""), suite_dir)
    title = str(payload.get("title") or spec_path)
    if not spec_path and not title:
        return None
    raw_status = str(payload.get("status") or "")
    status = _STATUS.get(raw_status) or _STATUS.get(raw_status.lower()) or TestResultStatus.FAILED
    error_message = str(payload.get("error") or "").strip() or None
    expected, actual = assertion_values(error_message, status)
    script = _match_script(spec_path, {_norm(item.file_path): item for item in scripts})
    if script and script.expected_result and status == TestResultStatus.PASSED:
        expected = script.expected_result
        actual = script.expected_result
    elif script and script.expected_result and not expected:
        expected = script.expected_result
    attachments = _attachments({"attachments": payload.get("attachments") or []}, suite_dir)
    console_log, network_log = extract_trace_logs(attachments.get("trace"))
    return RawTestOutcome(
        spec_path=script.file_path if script else spec_path,
        title=title,
        status=status,
        duration_ms=_duration_ms({"duration": payload.get("duration")}),
        error_message=error_message,
        expected=expected,
        actual=actual,
        screenshot=_read_bytes(attachments.get("screenshot")),
        video=_read_bytes(attachments.get("video")),
        trace=_read_bytes(attachments.get("trace")),
        console_log=console_log,
        network_log=network_log,
        script_id=script.id if script else None,
    )


def parse_playwright_json(
    report: dict[str, Any],
    suite_dir: Path,
    scripts: list[ScriptSnapshot],
) -> list[RawTestOutcome]:
    specs: list[dict[str, Any]] = []
    for suite in report.get("suites") or []:
        _collect_specs(suite, specs)
    by_path = {_norm(script.file_path): script for script in scripts}
    outcomes: list[RawTestOutcome] = []
    for spec in specs:
        spec_path = _relative(str(spec.get("file") or ""), suite_dir)
        title = str(spec.get("title") or spec_path)
        tests = spec.get("tests") or []
        last = _last_result(tests)
        status = _result_status(last, spec)
        error_message = _error_message(last)
        expected, actual = assertion_values(error_message, status)
        script = _match_script(spec_path, by_path)
        if script and script.expected_result and status == TestResultStatus.PASSED:
            expected = script.expected_result
            actual = script.expected_result
        elif script and script.expected_result and not expected:
            expected = script.expected_result
        attachments = _attachments(last, suite_dir)
        console_log, network_log = extract_trace_logs(attachments.get("trace"))
        outcomes.append(
            RawTestOutcome(
                spec_path=script.file_path if script else spec_path,
                title=title,
                status=status,
                duration_ms=_duration_ms(last),
                error_message=error_message,
                expected=expected,
                actual=actual,
                screenshot=_read_bytes(attachments.get("screenshot")),
                video=_read_bytes(attachments.get("video")),
                trace=_read_bytes(attachments.get("trace")),
                console_log=console_log,
                network_log=network_log,
                script_id=script.id if script else None,
            )
        )
    return outcomes


def assertion_values(error_message: str | None, status: TestResultStatus) -> tuple[str, str]:
    if error_message:
        matched = _EXPECT.search(error_message)
        if matched:
            return matched.group("expected").strip(), matched.group("actual").strip()
    if status == TestResultStatus.PASSED:
        return "pass", "pass"
    if status == TestResultStatus.SKIPPED:
        return "skipped", "skipped"
    return "pass", (error_message or "failed")[:2000]


def extract_trace_logs(trace_path: Path | None) -> tuple[bytes | None, bytes | None]:
    if trace_path is None or not trace_path.is_file():
        return _json_bytes([]), _json_bytes([])
    console: list[dict[str, Any]] = []
    network: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(trace_path) as archive:
            for name in archive.namelist():
                if not name.endswith(".trace"):
                    continue
                for raw_line in archive.read(name).splitlines():
                    try:
                        event = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    method = str(event.get("method") or event.get("type") or "")
                    params = event.get("params") if isinstance(event.get("params"), dict) else {}
                    lowered = method.lower()
                    if "console" in lowered:
                        console.append(params or {"type": method})
                    if lowered in {"request", "response", "requestfinished", "requestfailed"} or "network" in lowered:
                        network.append(params or {"type": method})
    except (OSError, zipfile.BadZipFile):
        return _json_bytes([]), _json_bytes([])
    return _json_bytes(console), _json_bytes(network)


def _collect_specs(suite: dict[str, Any], acc: list[dict[str, Any]]) -> None:
    acc.extend(suite.get("specs") or [])
    for child in suite.get("suites") or []:
        if isinstance(child, dict):
            _collect_specs(child, acc)


def _last_result(tests: list[Any]) -> dict[str, Any]:
    for test in reversed(tests):
        if not isinstance(test, dict):
            continue
        results = test.get("results") or []
        if results:
            last = results[-1]
            if isinstance(last, dict):
                return last
        status = test.get("status")
        if status:
            return {"status": status}
    return {}


def _result_status(result: dict[str, Any], spec: dict[str, Any]) -> TestResultStatus:
    raw = str(result.get("status") or spec.get("ok"))
    if raw in {"True", "true"}:
        return TestResultStatus.PASSED
    if raw in {"False", "false"}:
        return TestResultStatus.FAILED
    mapped = _STATUS.get(raw) or _STATUS.get(raw.lower())
    if mapped:
        return mapped
    if spec.get("ok") is True:
        return TestResultStatus.PASSED
    return TestResultStatus.FAILED


def _error_message(result: dict[str, Any]) -> str | None:
    error = result.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("value")
        if message:
            return str(message)[:4000]
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("message") or first)[:4000]
        return str(first)[:4000]
    return None


def _duration_ms(result: dict[str, Any]) -> int | None:
    duration = result.get("duration")
    try:
        return int(duration) if duration is not None else None
    except (TypeError, ValueError):
        return None


def _attachments(result: dict[str, Any], suite_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for item in result.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        path_str = item.get("path")
        if not path_str:
            continue
        path = Path(str(path_str))
        if not path.is_absolute():
            path = suite_dir / path
        if not path.is_file():
            continue
        if "screenshot" in name:
            found["screenshot"] = path
        elif "video" in name:
            found["video"] = path
        elif "trace" in name:
            found["trace"] = path
    return found


def _read_bytes(path: Path | None) -> bytes | None:
    if path is None or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _relative(path_str: str, suite_dir: Path) -> str:
    if not path_str:
        return ""
    path = Path(path_str)
    try:
        resolved = path.resolve()
        return resolved.relative_to(suite_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix().replace("\\", "/")


def _match_script(spec_path: str, by_path: dict[str, ScriptSnapshot]) -> ScriptSnapshot | None:
    normalized = _norm(spec_path)
    if normalized in by_path:
        return by_path[normalized]
    for file_path, script in by_path.items():
        if normalized.endswith(file_path) or file_path.endswith(normalized):
            return script
    return None


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _json_bytes(payload: list[dict[str, Any]]) -> bytes:
    return json.dumps(payload).encode("utf-8")
