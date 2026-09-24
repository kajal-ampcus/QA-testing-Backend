"""Behavioral checks for deterministic discovery and safety boundaries."""

from contextlib import asynccontextmanager
from typing import Any

import pytest

from core.agents.application_discovery.crawler import CrawlBudget, Crawler
from core.agents.application_discovery.parallel_crawler import ParallelCrawler


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


class DashboardBrowser(FakeBrowser):
    def __init__(self) -> None:
        super().__init__()
        self.authenticated = False

    async def authenticate(self) -> None:
        self.authenticated = True
        self.page = "dashboard"

    async def take_snapshot(self) -> list[TextBlock]:
        snapshots = {
            "root": (
                'uid=1_0 RootWebArea "Login" url="https://sample.test/login"\n'
                'uid=1_1 textbox "Username"\nuid=1_2 button "Log in"'
            ),
            "dashboard": (
                'uid=2_0 RootWebArea "Dashboard" url="https://sample.test/dashboard"\n'
                'uid=2_1 link "Users" url="https://sample.test/dashboard/users"\n'
                'uid=2_2 link "Reports" url="https://sample.test/dashboard/reports"'
            ),
            "users": (
                'uid=3_0 RootWebArea "Users" url="https://sample.test/dashboard/users"\n'
                'uid=3_1 button "User details"'
            ),
            "user-details": (
                'uid=4_0 RootWebArea "User details" url="https://sample.test/dashboard/users/1"\n'
                'uid=4_1 heading "User details"'
            ),
            "reports": (
                'uid=5_0 RootWebArea "Reports" url="https://sample.test/dashboard/reports"\n'
                'uid=5_1 heading "Reports"'
            ),
        }
        return [TextBlock(snapshots[self.page])]

    async def navigate_page(self, url: str) -> None:
        if url.endswith("/dashboard/users"):
            self.page = "users"
        elif url.endswith("/dashboard/reports"):
            self.page = "reports"
        else:
            self.page = "dashboard" if self.authenticated else "root"

    async def click(self, element_ref: str) -> None:
        if element_ref == "3_1":
            self.page = "user-details"


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


@pytest.mark.asyncio
async def test_parallel_crawler_uses_isolated_pool_and_preserves_flow_edges() -> None:
    browsers: list[FakeBrowser] = []

    @asynccontextmanager
    async def browser_context():
        browser = FakeBrowser()
        browsers.append(browser)
        yield browser

    states: list[dict[str, Any]] = []

    async def record(state: dict[str, Any]) -> None:
        states.append(state)

    crawler = ParallelCrawler(
        browser_context,
        CrawlBudget(max_pages=5, max_depth=2),
        [],
        login_url=None,
        authenticate=False,
        worker_limit=3,
    )
    status = await crawler.crawl("https://sample.test/", record)

    assert status == "COMPLETE"
    assert len(browsers) == 3
    assert {state["url_pattern"] for state in states} == {"/", "/details"}
    graph = crawler.coverage["app_flow_graph"]
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["action"] == "click(role=button,name='Details')"


@pytest.mark.asyncio
async def test_automatic_discovery_reports_internal_safety_breaker() -> None:
    @asynccontextmanager
    async def browser_context():
        yield FakeBrowser()

    crawler = ParallelCrawler(
        browser_context,
        CrawlBudget(max_pages=10000, max_depth=0, max_duration_seconds=21600, automatic_limits=True),
        [],
        login_url=None,
        authenticate=False,
        worker_limit=2,
    )
    status = await crawler.crawl("https://sample.test/", lambda state: _noop())

    assert status == "PARTIAL"
    assert crawler.termination_reason == "SAFETY_LIMIT_REACHED"
    assert crawler.coverage["safety_limit_kind"] == "depth"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "areas", "modules", "expected_urls"),
    [
        ("inventory", [], [], {"/login", "/dashboard"}),
        (
            "deep",
            ["dashboard"],
            ["users"],
            {"/login", "/dashboard", "/dashboard/users", "/dashboard/users/1"},
        ),
        (
            "deep",
            ["dashboard"],
            [],
            {
                "/login",
                "/dashboard",
                "/dashboard/users",
                "/dashboard/users/1",
                "/dashboard/reports",
            },
        ),
    ],
)
async def test_scoped_dashboard_discovery(mode, areas, modules, expected_urls) -> None:
    browsers: list[DashboardBrowser] = []

    @asynccontextmanager
    async def browser_context():
        browser = DashboardBrowser()
        browsers.append(browser)
        yield browser

    states: list[dict[str, Any]] = []
    crawler = ParallelCrawler(
        browser_context,
        CrawlBudget(max_pages=20, max_depth=5),
        [],
        login_url="https://sample.test/login",
        authenticate=True,
        worker_limit=2,
        discovery_mode=mode,
        selected_areas=areas,
        selected_modules=modules,
    )

    async def record(state: dict[str, Any]) -> None:
        states.append(state)

    await crawler.crawl("https://sample.test/", record)

    assert {state["url_pattern"] for state in states} == expected_urls
    catalog = crawler.coverage["discovery_catalog"]
    assert {area["id"] for area in catalog["areas"]} >= {"login", "dashboard"}
    assert {module["id"] for module in catalog["modules"]} >= {"users", "reports"}
    graph = crawler.coverage["app_flow_graph"]
    assert any(edge["action"] == "authenticate(completion=dashboard)" for edge in graph["edges"])


@pytest.mark.asyncio
async def test_resume_crosses_old_page_limit_without_rediscovering_completed_states() -> None:
    checkpoints: list[dict[str, Any]] = []

    @asynccontextmanager
    async def browser_context():
        yield DashboardBrowser()

    first_states: list[dict[str, Any]] = []

    async def save_checkpoint(value: dict[str, Any]) -> None:
        checkpoints.append(value)

    async def record_first(state: dict[str, Any]) -> None:
        first_states.append(state)

    first = ParallelCrawler(
        browser_context,
        CrawlBudget(max_pages=2, max_depth=5, automatic_limits=False),
        [],
        login_url="https://sample.test/login",
        authenticate=True,
        worker_limit=1,
        discovery_mode="deep",
        selected_areas=["dashboard"],
        on_checkpoint=save_checkpoint,
    )
    assert await first.crawl("https://sample.test/", record_first) == "PARTIAL"
    assert first.termination_reason == "MAX_PAGES_REACHED"

    completed_fingerprints = {state["fingerprint"] for state in first_states}
    resumed_states: list[dict[str, Any]] = []

    async def record_resumed(state: dict[str, Any]) -> None:
        resumed_states.append(state)

    resumed = ParallelCrawler(
        browser_context,
        CrawlBudget(max_pages=10, max_depth=5, automatic_limits=False),
        [],
        login_url="https://sample.test/login",
        authenticate=True,
        worker_limit=1,
        discovery_mode="deep",
        selected_areas=["dashboard"],
        checkpoint=checkpoints[-1],
        on_checkpoint=save_checkpoint,
    )
    assert await resumed.crawl("https://sample.test/", record_resumed) == "COMPLETE"
    assert completed_fingerprints.isdisjoint(
        {state["fingerprint"] for state in resumed_states}
    )
    assert resumed.coverage["states_discovered"] > len(first_states)
    assert not checkpoints[-1]["pending_nodes"]
    assert not checkpoints[-1]["failed_nodes"]


@pytest.mark.asyncio
async def test_resume_with_increased_depth_expands_saved_boundary() -> None:
    checkpoints: list[dict[str, Any]] = []

    @asynccontextmanager
    async def browser_context():
        yield FakeBrowser()

    async def save_checkpoint(value: dict[str, Any]) -> None:
        checkpoints.append(value)

    first = ParallelCrawler(
        browser_context,
        CrawlBudget(max_pages=5, max_depth=0, automatic_limits=False),
        [],
        login_url=None,
        authenticate=False,
        worker_limit=1,
        on_checkpoint=save_checkpoint,
    )
    first_states: list[dict[str, Any]] = []

    async def record_first(state: dict[str, Any]) -> None:
        first_states.append(state)

    assert await first.crawl("https://sample.test/", record_first) == "PARTIAL"
    assert first.termination_reason == "MAX_DEPTH_REACHED"

    resumed_states: list[dict[str, Any]] = []
    resumed = ParallelCrawler(
        browser_context,
        CrawlBudget(max_pages=5, max_depth=2, automatic_limits=False),
        [],
        login_url=None,
        authenticate=False,
        worker_limit=1,
        checkpoint=checkpoints[-1],
        on_checkpoint=save_checkpoint,
    )

    async def record_resumed(state: dict[str, Any]) -> None:
        resumed_states.append(state)

    assert await resumed.crawl("https://sample.test/", record_resumed) == "COMPLETE"
    assert {state["url_pattern"] for state in resumed_states} == {"/details"}


@pytest.mark.asyncio
async def test_separate_runs_merge_into_one_deduplicated_application_graph() -> None:
    public_browsers: list[RegistrationBrowser] = []

    @asynccontextmanager
    async def public_context():
        browser = RegistrationBrowser()
        public_browsers.append(browser)
        yield browser

    public = ParallelCrawler(
        public_context,
        CrawlBudget(max_pages=10, max_depth=3),
        [],
        login_url="https://sample.test/login",
        authenticate=False,
        worker_limit=1,
        discovery_mode="complete",
    )
    public_states: list[dict[str, Any]] = []

    async def record_public(state: dict[str, Any]) -> None:
        public_states.append(state)

    await public.crawl("https://sample.test/", record_public)
    first_graph = public.coverage["app_flow_graph"]
    checkpoint = {
        "version": 1,
        "jobs": [],
        "completed_nodes": [],
        "graph": first_graph,
        "modules": public.coverage["discovery_catalog"]["modules"],
    }

    @asynccontextmanager
    async def authenticated_context():
        yield RegistrationBrowser()

    authenticated = ParallelCrawler(
        authenticated_context,
        CrawlBudget(max_pages=10, max_depth=3),
        [],
        login_url="https://sample.test/login",
        authenticate=True,
        worker_limit=1,
        discovery_mode="deep",
        selected_areas=["dashboard"],
        checkpoint=checkpoint,
    )
    newly_persisted: list[dict[str, Any]] = []

    async def record_authenticated(state: dict[str, Any]) -> None:
        newly_persisted.append(state)

    await authenticated.crawl("https://sample.test/", record_authenticated)
    combined = authenticated.coverage["app_flow_graph"]
    fingerprints = [node["fingerprint"] for node in combined["nodes"]]

    assert len(fingerprints) == len(set(fingerprints))
    assert {node["url_pattern"] for node in combined["nodes"]} == {
        "/login",
        "/register",
        "/dashboard",
    }
    assert {state["url_pattern"] for state in newly_persisted} == {"/dashboard"}
    assert any(
        edge["action"] == "authenticate(completion=dashboard)"
        for edge in combined["edges"]
    )


async def _noop() -> None:
    return None
