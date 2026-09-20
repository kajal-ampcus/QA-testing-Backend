"""Behavioral checks for deterministic discovery and safety boundaries."""

from typing import Any

import pytest

from core.agents.application_discovery.crawler import CrawlBudget, Crawler


class TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeBrowser:
    def __init__(self) -> None:
        self.page = "root"
        self.clicked: list[str] = []

    async def navigate_page(self, url: str) -> None:
        self.page = "root"

    async def authenticate(self) -> None:
        pass

    async def wait_until_ready(self) -> None:
        pass

    async def take_snapshot(self) -> list[TextBlock]:
        if self.page == "root":
            text = (
                'uid=1_0 RootWebArea "Sample" url="https://sample.test/"\n'
                'uid=1_1 button "Details"\n'
                'uid=1_2 button "Delete account"\n'
                'uid=1_3 link "External" url="https://elsewhere.test/"'
            )
        else:
            text = (
                'uid=1_0 RootWebArea "Details" url="https://sample.test/details"\n'
                'uid=1_1 heading "Details"'
            )
        return [TextBlock(text)]

    async def click(self, element_ref: str) -> None:
        self.clicked.append(element_ref)
        if element_ref == "1_1":
            self.page = "details"

    async def fill(self, element_ref: str, value: str) -> None:
        pass

    async def clear_cookies(self) -> None:
        pass

    async def handle_dialog(self, action: str = "dismiss") -> None:
        pass

    async def list_console_messages(self) -> list[Any]:
        return []

    async def list_network_requests(self) -> list[Any]:
        return []

    async def take_screenshot(self) -> str:
        return ""


class RegistrationBrowser(FakeBrowser):
    def __init__(self) -> None:
        super().__init__()
        self.authenticated = False

    async def navigate_page(self, url: str) -> None:
        if url.endswith("/register"):
            self.page = "register"
        else:
            self.page = "root"

    async def authenticate(self) -> None:
        self.authenticated = True
        self.page = "dashboard"

    async def take_snapshot(self) -> list[TextBlock]:
        if self.page == "root":
            text = (
                'uid=1_0 RootWebArea "Login" url="https://sample.test/login"\n'
                'uid=1_1 textbox "Username"\n'
                'uid=1_2 button "Log in"\n'
                'uid=1_3 link "Register" url="https://sample.test/register"'
            )
        elif self.page == "register":
            text = (
                'uid=2_0 RootWebArea "Register" url="https://sample.test/register"\n'
                'uid=2_1 textbox "Username"\n'
                'uid=2_2 button "Create account"'
            )
        else:
            text = (
                'uid=3_0 RootWebArea "Dashboard" url="https://sample.test/dashboard"\n'
                'uid=3_1 heading "Dashboard"'
            )
        return [TextBlock(text)]


@pytest.mark.asyncio
async def test_crawler_records_observed_states_without_destructive_or_external_clicks() -> None:
    browser = FakeBrowser()
    states: list[dict[str, Any]] = []

    async def record(state: dict[str, Any]) -> None:
        states.append(state)

    crawler = Crawler(browser, CrawlBudget(max_pages=5, max_depth=2), [])
    status = await crawler.crawl("https://sample.test/", record)

    assert status == "COMPLETE"
    assert len(states) == 2
    assert crawler.coverage["states_discovered"] == 2
    assert {state["url_pattern"] for state in states} == {"/", "/details"}
    assert browser.clicked == ["1_1"]
    assert any(
        element["risk"] == "DESTRUCTIVE" and element["name"] == "Delete account"
        for element in states[0]["elements"]
    )
    assert all(
        element["source"] == "OBSERVED_DOM" for state in states for element in state["elements"]
    )


@pytest.mark.asyncio
async def test_unchanged_crawl_has_stable_fingerprints() -> None:
    async def fingerprints() -> list[str]:
        states: list[dict[str, Any]] = []

        async def record(state: dict[str, Any]) -> None:
            states.append(state)

        await Crawler(FakeBrowser(), CrawlBudget(max_pages=5, max_depth=2), []).crawl(
            "https://sample.test/", record
        )
        return [state["fingerprint"] for state in states]

    assert await fingerprints() == await fingerprints()


@pytest.mark.asyncio
async def test_crawler_follows_safe_registration_link_and_records_authentication_edge() -> None:
    browser = RegistrationBrowser()
    states: list[dict[str, Any]] = []

    async def record(state: dict[str, Any]) -> None:
        states.append(state)

    crawler = Crawler(
        browser,
        CrawlBudget(max_pages=5, max_depth=2),
        [],
        login_url="https://sample.test/login",
        authenticate=True,
    )
    status = await crawler.crawl("https://sample.test/", record)

    by_url = {state["url_pattern"]: state for state in states}
    assert status == "COMPLETE"
    assert set(by_url) == {"/login", "/register", "/dashboard"}
    assert by_url["/register"]["reached_via"] == [
        "navigate(url='https://sample.test/register',observed_link='Register')"
    ]
    assert by_url["/dashboard"]["reached_via"] == [
        "click(role=authentication,name='Log in')"
    ]
    assert all("link Register" not in action for action in crawler.coverage["skipped_actions"])
    assert any("button Create account" in action for action in crawler.coverage["skipped_actions"])
