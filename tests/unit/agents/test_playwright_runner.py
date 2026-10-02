import json
import zipfile
from pathlib import Path
from uuid import uuid4

from core.agents.test_execution.eligibility import ScriptSnapshot
from core.agents.test_execution.playwright_runner import (
    assertion_values,
    extract_trace_logs,
    parse_playwright_json,
)
from domain.enums import TestResultStatus


def test_assertion_values_parse_playwright_expect_output() -> None:
    message = "Error: expect(locator).toHaveURL failed\nExpected: /dashboard\nReceived: /login\n"
    expected, actual = assertion_values(message, TestResultStatus.FAILED)
    assert expected == "/dashboard"
    assert actual == "/login"


def test_assertion_values_pass_is_plain_comparison() -> None:
    expected, actual = assertion_values(None, TestResultStatus.PASSED)
    assert expected == actual == "pass"


def test_parse_playwright_json_maps_spec_and_status(tmp_path: Path) -> None:
    script = ScriptSnapshot(
        id=uuid4(),
        file_path="tests/login.spec.ts",
        risk_level="SAFE",
        review_status="REVIEWED",
        expected_result="User reaches dashboard",
        test_case_code="TC-001",
    )
    report = {
        "suites": [
            {
                "title": "tests/login.spec.ts",
                "file": str(tmp_path / "tests" / "login.spec.ts"),
                "specs": [
                    {
                        "title": "logs in",
                        "ok": True,
                        "file": str(tmp_path / "tests" / "login.spec.ts"),
                        "tests": [
                            {
                                "status": "expected",
                                "results": [{"status": "passed", "duration": 42, "attachments": []}],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    outcomes = parse_playwright_json(report, tmp_path, [script])
    assert len(outcomes) == 1
    assert outcomes[0].script_id == script.id
    assert outcomes[0].status == TestResultStatus.PASSED
    assert outcomes[0].duration_ms == 42
    assert outcomes[0].expected == "User reaches dashboard"


def test_parse_failed_result_uses_error_message(tmp_path: Path) -> None:
    script = ScriptSnapshot(
        id=uuid4(),
        file_path="tests/fail.spec.ts",
        risk_level="SAFE",
        review_status="REVIEWED",
    )
    report = {
        "suites": [
            {
                "specs": [
                    {
                        "title": "breaks",
                        "file": "tests/fail.spec.ts",
                        "ok": False,
                        "tests": [
                            {
                                "results": [
                                    {
                                        "status": "failed",
                                        "error": {
                                            "message": "Expected: 201\nReceived: 500\n",
                                        },
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        ]
    }
    outcomes = parse_playwright_json(report, tmp_path, [script])
    assert outcomes[0].status == TestResultStatus.FAILED
    assert outcomes[0].expected == "201"
    assert outcomes[0].actual == "500"


def test_extract_trace_logs_reads_console_and_network(tmp_path: Path) -> None:
    trace = tmp_path / "trace.zip"
    with zipfile.ZipFile(trace, "w") as archive:
        events = "\n".join(
            [
                json.dumps({"method": "console", "params": {"text": "hello", "type": "log"}}),
                json.dumps({"method": "request", "params": {"url": "https://example.test", "method": "GET"}}),
            ]
        )
        archive.writestr("trace.trace", events)
    console, network = extract_trace_logs(trace)
    assert console is not None and b"hello" in console
    assert network is not None and b"https://example.test" in network
