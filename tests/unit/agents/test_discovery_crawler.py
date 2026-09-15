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


@pytest.mark.asyncio
async def test_crawler_records_observed_states_without_destructive_or_external_clicks() -> None:
    browser = FakeBrowser()
    states: list[dict[str, Any]] = []

    async def record(state: dict[str, Any]) -> None:
        states.append(state)

    status = await Crawler(browser, CrawlBudget(max_pages=5, max_depth=2), []).crawl(
        "https://sample.test/", record
    )

    assert status == "COMPLETE"
    assert len(states) == 2
    assert {state["url_pattern"] for state in states} == {"/", "/details"}
    assert browser.clicked == ["1_1"]
    assert any(
        element["risk"] == "DESTRUCTIVE" and element["name"] == "Delete account"
        for element in states[0]["elements"]
    )
    assert all(element["source"] == "OBSERVED_DOM" for state in states for element in state["elements"])


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
