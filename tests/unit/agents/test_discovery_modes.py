from contextlib import asynccontextmanager

import pytest

from core.agents.application_discovery.crawler import CrawlBudget
from core.agents.application_discovery.parallel_crawler import (
    ParallelCrawler,
    _is_login_form_chrome,
)
from tests.unit.agents.test_discovery_crawler import DashboardBrowser, FakeBrowser, TextBlock


class SessionBrowser(DashboardBrowser):
    logins = 0
    imports = 0

    async def authenticate(self):
        type(self).logins += 1
        await super().authenticate()

    async def export_authenticated_session(self):
        return {"authenticated": self.authenticated}

    async def import_authenticated_session(self, state):
        type(self).imports += 1
        self.authenticated = state["authenticated"]


@asynccontextmanager
async def factory():
    yield SessionBrowser()


async def record(_state):
    pass


@pytest.mark.asyncio
async def test_full_authenticates_once_and_merges_parallel_branches():
    SessionBrowser.logins = SessionBrowser.imports = 0
    crawler = ParallelCrawler(factory, CrawlBudget(max_pages=30, max_depth=8), [],
        login_url="https://sample.test/login", authenticate=True, discovery_mode="full")
    assert await crawler.crawl("https://sample.test/", record) == "COMPLETE"
    assert SessionBrowser.logins == 1
    assert SessionBrowser.imports >= 1
    graph = crawler.coverage["app_flow_graph"]
    assert {node["url_pattern"] for node in graph["nodes"]} >= {
        "/login", "/dashboard", "/dashboard/users", "/dashboard/reports"}
    assert any(edge["action"] == "authenticate" for edge in graph["edges"])
    assert crawler.coverage["progress"]["active_workers"] == 0
    assert crawler.coverage["queue_exhausted"]


@pytest.mark.asyncio
async def test_targeted_inventory_then_selected_observed_branch():
    inventory = ParallelCrawler(factory, CrawlBudget(max_pages=30, max_depth=8), [],
        login_url="https://sample.test/login", authenticate=True, discovery_mode="targeted")
    assert await inventory.crawl("https://sample.test/", record) == "COMPLETE"
    assert {node["url_pattern"] for node in inventory.coverage["app_flow_graph"]["nodes"]} == {"/login", "/dashboard"}
    assert {item["id"] for item in inventory.coverage["discovery_catalog"]["modules"]} == {"users", "reports"}
    crawler = ParallelCrawler(factory, CrawlBudget(max_pages=30, max_depth=8), [],
        login_url="https://sample.test/login", authenticate=True, discovery_mode="targeted",
        selected_modules=["users"], checkpoint=inventory._checkpoint_payload())
    assert await crawler.crawl("https://sample.test/", record) == "COMPLETE"
    urls = {node["url_pattern"] for node in crawler.coverage["app_flow_graph"]["nodes"]}
    assert "/dashboard/users" in urls
    assert "/dashboard/reports" not in urls


@pytest.mark.parametrize("name,url", [("Log out", "/logout"), ("Continue", "/checkout"), ("Delete", "/record"), ("Pay", "/payment")])
def test_unsafe_links_are_rejected(name, url):
    assert not ParallelCrawler._safe_navigation({"role": "link", "name": name, "url": url})


def test_login_role_buttons_are_not_crawl_actions():
    login_form = [
        {"role": "textbox", "name": "Enter your password"},
        {"role": "button", "name": "Kitchen"},
        {"role": "link", "name": "Forgot Password", "url": "/forgot-password"},
    ]
    assert _is_login_form_chrome(login_form, "button", None)
    assert not _is_login_form_chrome(login_form, "link", "/forgot-password")
    assert not _is_login_form_chrome(
        [{"role": "button", "name": "Kitchen", "url": "/kitchen"}],
        "button",
        "/kitchen",
    )


@pytest.mark.asyncio
async def test_full_uses_observed_landing_route_and_resumes_page_boundary():
    class WorkspaceBrowser(SessionBrowser):
        async def navigate_page(self, url):
            await super().navigate_page(url.replace("/workspace/overview", "/dashboard"))

        async def take_snapshot(self):
            return [TextBlock(block.text.replace("/dashboard", "/workspace/overview"))
                for block in await super().take_snapshot()]

    @asynccontextmanager
    async def workspace():
        yield WorkspaceBrowser()

    first = ParallelCrawler(workspace, CrawlBudget(max_pages=3, max_depth=8), [],
        login_url="https://sample.test/login", authenticate=True, discovery_mode="full")
    assert await first.crawl("https://sample.test/", record) == "PARTIAL"
    checkpoint = first._checkpoint_payload()
    assert checkpoint["pending_nodes"]
    resumed = ParallelCrawler(workspace, CrawlBudget(max_pages=30, max_depth=8), [],
        login_url="https://sample.test/login", authenticate=True, discovery_mode="full", checkpoint=checkpoint)
    assert await resumed.crawl("https://sample.test/", record) == "COMPLETE"
    urls = {node["url_pattern"] for node in resumed.coverage["app_flow_graph"]["nodes"]}
    assert "/workspace/overview/reports" in urls
    assert not any("dashboard" in url for url in urls)


@pytest.mark.asyncio
async def test_public_targeted_inventory_uses_observed_actions():
    @asynccontextmanager
    async def public():
        yield FakeBrowser()
    crawler = ParallelCrawler(public, CrawlBudget(max_pages=20), [],
        login_url=None, authenticate=False, discovery_mode="targeted")
    assert await crawler.crawl("https://sample.test/", record) == "COMPLETE"
    assert [module["label"] for module in crawler.coverage["discovery_catalog"]["modules"]] == ["Details"]


def test_signature_distinguishes_hash_routes_and_ignores_transport_tokens():
    from core.agents.application_discovery.fingerprint import compute_fingerprint
    assert compute_fingerprint("https://sample.test/#/one", "page") != compute_fingerprint("https://sample.test/#/two", "page")
    assert compute_fingerprint("https://sample.test/?token=one", "page") == compute_fingerprint("https://sample.test/?token=two", "page")


def test_menu_toggle_is_not_a_functional_action():
    from core.agents.application_discovery.crawler import _is_chrome_navigation_control
    from core.agents.application_discovery.fingerprint import auth_flow_label

    assert _is_chrome_navigation_control("button", "Menu", None)
    assert not _is_chrome_navigation_control("link", "Menu", "/menu")
    assert auth_flow_label("login") == "Login"
    assert auth_flow_label("recovery") == "Forgot password"


@pytest.mark.asyncio
async def test_live_view_tracks_the_screen_workers_are_on():
    @asynccontextmanager
    async def public():
        yield FakeBrowser()

    crawler = ParallelCrawler(
        public,
        CrawlBudget(max_pages=20),
        [],
        login_url=None,
        authenticate=False,
        discovery_mode="targeted",
        worker_limit=2,
    )
    assert await crawler.crawl("https://sample.test/", record) == "COMPLETE"
    assert crawler._live_view
    assert crawler._live_view["label"]
    assert crawler.coverage["live_view"]["url"]
    labels = {node.get("label") for node in crawler.coverage["app_flow_graph"]["nodes"]}
    assert "Details" in labels or crawler._live_view["label"] in {"Sample", "Details", "Public entry", "Page"}
