from pathlib import Path

from core.agents.automation_review.lint_rules import lint_suite
from core.agents.automation_review.node_verify import LIST_ARGS, verify_suite


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_lint_counts_sleep_xpath_secrets_and_unsafe_destructive_specs(tmp_path: Path) -> None:
    _write(
        tmp_path / "pages" / "home.page.ts",
        """
        export class HomePage {
          async go(page) {
            page.goto("/home");
            await page.waitForTimeout(1000);
            await page.locator("xpath=//button");
          }
        }
        """,
    )
    _write(
        tmp_path / "tests" / "general" / "TC-001.login.spec.ts",
        """
        test.only("login", async ({ page }) => {
          const password = "Sup3rSecret!";
          await page.getByRole("button").click();
        });
        """,
    )
    _write(
        tmp_path / "tests" / "destructive" / "TC-002.delete.spec.ts",
        """
        test("delete", async ({ page }) => {
          await page.getByRole("button", { name: "Delete" }).click();
        });
        """,
    )
    _write(
        tmp_path / "tests" / "blocked" / "TC-003.blocked.spec.ts",
        "test.fixme('blocked', async () => {});\n",
    )
    result = lint_suite(tmp_path, forbidden_literals=["Sup3rSecret!"])
    assert result.hardcoded_sleep == 1
    assert result.xpath_fallback == 1
    assert result.literal_credentials >= 1
    assert result.test_only == 1
    assert result.missing_await == 1
    assert result.missing_traceability == 3
    assert result.unsupported_blocked_selectors == 1
    assert result.destructive_not_skipped == 1
    assert all("Sup3rSecret!" not in item["message"] for item in result.findings)


class Completed:
    def __init__(self, code: int) -> None:
        self.returncode = code


def test_node_verification_is_not_verified_without_node(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("core.agents.automation_review.node_verify.shutil.which", lambda _name: None)
    result = verify_suite(tmp_path)
    assert result.status == "NOT_VERIFIED"
    assert result.tsc == "NOT_VERIFIED"
    assert result.playwright_list == "NOT_VERIFIED"
    assert result.commands == []


def test_node_verification_lists_tests_without_executing_them(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "core.agents.automation_review.node_verify.shutil.which",
        lambda name: name,
    )
    seen: list[list[str]] = []

    def runner(command, **_kwargs):
        seen.append(command)
        return Completed(0)

    result = verify_suite(tmp_path, runner=runner)
    assert result.status == "PASSED"
    assert result.tsc == "PASSED"
    assert result.playwright_list == "PASSED"
    assert seen[-1][-3:] == LIST_ARGS
    assert not any(command[-2:] == ["test"] or command[-1:] == ["test"] for command in seen)


def test_failed_typecheck_is_failed_not_passed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "core.agents.automation_review.node_verify.shutil.which",
        lambda name: name,
    )

    def runner(command, **_kwargs):
        return Completed(0 if "install" in command[1] else 1)

    result = verify_suite(tmp_path, runner=runner)
    assert result.status == "FAILED"
    assert result.status != "PASSED"
