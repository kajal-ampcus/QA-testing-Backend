"""Which language and framework pair can write a suite, and which can run here.

The current writer is TypeScript Playwright. Python and Java are accepted
choices so the request can name them, and they stay unwired until a template
module exists.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.agents.automation_generation.suite import generate_suite

StackWriter = Callable[..., Any]

_KNOWN = {
    ("typescript", "playwright"),
    ("typescript", "selenium"),
    ("python", "playwright"),
    ("python", "selenium"),
    ("java", "playwright"),
    ("java", "selenium"),
}
_WRITERS: dict[tuple[str, str], StackWriter] = {
    ("typescript", "playwright"): generate_suite,
}


class StackChoiceError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def normalize_stack(language: str, framework: str) -> tuple[str, str]:
    """Return the canonical pair, or raise when it cannot write a suite."""
    key = (language.strip().lower(), framework.strip().lower())
    if key not in _KNOWN:
        raise StackChoiceError(
            f"{language.strip() or 'That language'} with {framework.strip() or 'that framework'} is not a supported automation stack."
        )
    if key not in _WRITERS:
        raise StackChoiceError(
            f"A writer for {key[0]} {key[1]} is not available yet. "
            "Choose TypeScript and Playwright to generate a suite that can run here."
        )
    return key


def writer_for(language: str, framework: str) -> StackWriter:
    key = normalize_stack(language, framework)
    return _WRITERS[key]


def execution_available(language: str | None, framework: str | None) -> bool:
    """Older suites omit language and were written as TypeScript Playwright."""
    chosen = ((language or "typescript").strip().lower(), (framework or "playwright").strip().lower())
    return chosen == ("typescript", "playwright")
