from core.agents.automation_generation.registry import (
    StackChoiceError,
    execution_available,
    normalize_stack,
    writer_for,
)
from core.agents.automation_generation.suite import generate_suite


def test_typescript_playwright_uses_the_existing_writer() -> None:
    assert normalize_stack("TypeScript", "Playwright") == ("typescript", "playwright")
    assert writer_for("typescript", "playwright") is generate_suite
    assert execution_available("typescript", "playwright")
    assert execution_available("", "playwright")


def test_declared_pairs_without_a_writer_are_rejected() -> None:
    for language, framework in (
        ("typescript", "selenium"),
        ("python", "playwright"),
        ("python", "selenium"),
        ("java", "playwright"),
        ("java", "selenium"),
    ):
        try:
            normalize_stack(language, framework)
        except StackChoiceError as exc:
            assert "not available yet" in exc.message
        else:
            raise AssertionError(f"{language} {framework} should not write a suite yet")
        assert not execution_available(language, framework)


def test_unknown_pair_is_rejected() -> None:
    try:
        normalize_stack("ruby", "capybara")
    except StackChoiceError as exc:
        assert "not a supported" in exc.message
    else:
        raise AssertionError("unknown stacks must be rejected")
