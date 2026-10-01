"""
Deterministic lint counts for a generated Playwright suite.

Counts are the result, not a single pass/fail flag. Findings name the file
and the rule. Secret values are never copied into a finding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {"node_modules", "test-results", "playwright-report", "blob-report"}
_SLEEP = re.compile(r"\b(waitForTimeout|setTimeout|page\.waitForTimeout)\s*\(|\bsleep\s*\(")
_TEST_ONLY = re.compile(r"\b(?:test|describe)\.only\b")
_XPATH = re.compile(r"xpath=|locator\(\s*['\"]//")
_SECRET_LITERAL = re.compile(
    r"""(?ix)
    (?:password|passwd|secret|api[_-]?key|token|credential(?:_ref)?)
    \s*[:=]\s*
    (['\"])
    (?!\1)
    ([^'\"]{3,})
    \1
    """
)
_CALL = re.compile(
    r"\.(?:goto|click|fill|press|check|uncheck|waitFor|waitForLoadState|toBeVisible|toContainText|toHaveURL)\s*\("
)
_TRACE_KEYS = (
    "project_id:",
    "test_case_id:",
    "test_case_version:",
    "application_map_id:",
    "application_map_version:",
)


@dataclass
class LintResult:
    hardcoded_sleep: int = 0
    literal_credentials: int = 0
    xpath_fallback: int = 0
    missing_traceability: int = 0
    unsupported_blocked_selectors: int = 0
    test_only: int = 0
    missing_await: int = 0
    destructive_not_skipped: int = 0
    findings: list[dict[str, str | int]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "hardcoded_sleep": self.hardcoded_sleep,
            "literal_credentials": self.literal_credentials,
            "xpath_fallback": self.xpath_fallback,
            "missing_traceability": self.missing_traceability,
            "unsupported_blocked_selectors": self.unsupported_blocked_selectors,
            "test_only": self.test_only,
            "missing_await": self.missing_await,
            "destructive_not_skipped": self.destructive_not_skipped,
            "findings": self.findings,
        }


def lint_suite(suite_dir: Path, *, forbidden_literals: list[str] | None = None) -> LintResult:
    result = LintResult()
    secrets = [item for item in (forbidden_literals or []) if item and len(item) >= 4]
    for path in _sources(suite_dir):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(suite_dir).as_posix()
        _scan_source(result, relative, text, secrets)
    return result


def _sources(suite_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(suite_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(suite_dir)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in {".ts", ".js", ".mjs"} or path.name in {"review-report.json"}:
            files.append(path)
    return files


def _scan_source(result: LintResult, relative: str, text: str, secrets: list[str]) -> None:
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "*", "/*")):
            continue
        if _SLEEP.search(stripped):
            result.hardcoded_sleep += 1
            _finding(result, relative, number, "hardcoded_sleep", "Hard-coded sleep or waitForTimeout.")
        if _TEST_ONLY.search(stripped):
            result.test_only += 1
            _finding(result, relative, number, "test_only", "test.only or describe.only is present.")
        if _XPATH.search(stripped):
            result.xpath_fallback += 1
            _finding(result, relative, number, "xpath_fallback", "XPath fallback locator.")
        if _SECRET_LITERAL.search(stripped) and "process.env" not in stripped:
            result.literal_credentials += 1
            _finding(result, relative, number, "literal_credentials", "Credential-like literal value.")
        elif any(secret in stripped for secret in secrets):
            result.literal_credentials += 1
            _finding(result, relative, number, "literal_credentials", "A forbidden secret value was written.")
        if _CALL.search(stripped) and "await " not in stripped and "function " not in stripped:
            result.missing_await += 1
            _finding(result, relative, number, "missing_await", "Playwright call is missing await.")
    if relative.endswith(".spec.ts"):
        if any(key not in text for key in _TRACE_KEYS):
            result.missing_traceability += 1
            _finding(result, relative, 1, "missing_traceability", "Spec is missing traceability fields.")
        if "/blocked/" in f"/{relative}" or relative.startswith("tests/blocked/"):
            result.unsupported_blocked_selectors += 1
            _finding(result, relative, 1, "unsupported_blocked_selectors", "Blocked spec was not given a locator.")
        if relative.startswith("tests/destructive/") and (
            "test.skip" not in text or "RUN_DESTRUCTIVE" not in text
        ):
            result.destructive_not_skipped += 1
            _finding(
                result,
                relative,
                1,
                "destructive_not_skipped",
                "Destructive spec is not skipped unless RUN_DESTRUCTIVE=true.",
            )


def _finding(result: LintResult, path: str, line: int, rule: str, message: str) -> None:
    result.findings.append({"path": path, "line": line, "rule": rule, "message": message})
