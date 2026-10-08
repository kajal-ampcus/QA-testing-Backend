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
@pytest.mark.parametrize("legacy_keys", [False, True])
async def test_guided_persists_siblings_and_expands_only_selected_page(legacy_keys):
    class BranchBrowser(FakeBrowser):
        async def navigate_page(self, url):
            self.page = url.rsplit("/", 1)[-1] or "root"

        async def take_snapshot(self):
            children = {"root": ["products", "about"], "products": ["phones", "clothes"]}
            url = "https://sample.test/" + ("" if self.page == "root" else self.page)
            return [TextBlock(f'uid=1 RootWebArea "{self.page}" url="{url}"\n' + "\n".join(
                f'uid={i + 2} link "{child}" url="https://sample.test/{child}"'
                for i, child in enumerate(children.get(self.page, []))
            ))]

    @asynccontextmanager
    async def browser():
        yield BranchBrowser()

    checkpoint = None
    observed = []

    async def save(state):
        observed.append(state["url_pattern"])

    async def run(selected=()):
        nonlocal checkpoint
        crawler = ParallelCrawler(browser, CrawlBudget(max_pages=30, max_depth=8), [],
            login_url=None, authenticate=False, discovery_mode="guided",
            checkpoint=checkpoint, selected_branches=list(selected))
        await crawler.crawl("https://sample.test/", save)
        checkpoint = crawler._checkpoint_payload()
        return crawler

    def branch(name):
        return next(job["key"] for job in checkpoint["jobs"]
            if job["path"] and job["path"][-1]["name"] == name)

    first = await run()
    assert observed == ["/"]
    assert first.termination_reason == "AWAITING_BRANCH_SELECTION"
    if legacy_keys:
        for job in checkpoint["jobs"]:
            job["key"] = job["key"].replace(":0", "")
            for step in job["path"]:
                step.pop("occurrence", None)
    await run([branch("products")])
    products = [job for job in checkpoint["jobs"]
        if job["path"] and job["path"][-1]["name"] == "products"]
    assert len(products) == 1
    assert products[0]["status"] == "completed"
    assert observed == ["/", "/products"]
    await run([branch("phones")])
    await run([branch("about")])
    assert "/clothes" not in observed
    await run([branch("clothes")])
    assert observed == ["/", "/products", "/phones", "/about", "/clothes"]
    assert len(checkpoint["graph"]["edges"]) == 4


@pytest.mark.parametrize("reverse", [False, True])
def test_checkpoint_merges_legacy_duplicates_without_merging_other_routes(reverse):
    from copy import deepcopy

    original = {"key": "public|button:Contact::|expand=False", "status": "available",
        "path": [{"role": "button", "name": "Contact"}], "skip_auth": True}
    completed = {**deepcopy(original), "key": "public|button:Contact:::0|expand=False",
        "status": "completed", "result_fingerprint": "contact-state"}
    other_route = {**deepcopy(original), "key": "other-route",
        "path": [{"role": "link", "name": "Home"}, {"role": "button", "name": "Contact"}]}
    other_occurrence = {**deepcopy(original), "key": "other-occurrence",
        "path": [{"role": "button", "name": "Contact", "occurrence": 1}]}
    jobs = [original, completed, other_route, other_occurrence]
    crawler = ParallelCrawler(factory, CrawlBudget(), [], login_url=None,
        authenticate=False, discovery_mode="guided",
        checkpoint={"jobs": list(reversed(jobs)) if reverse else jobs},
        selected_branches=[original["key"]])
    saved = crawler._checkpoint_payload()["jobs"]
    assert len(saved) == 3
    done = next(job for job in saved if job["status"] == "completed")
    assert done["result_fingerprint"] == "contact-state"
    assert crawler._selected_branches == {done["key"]}
    assert sum(job["status"] == "available" for job in saved) == 2


@pytest.mark.asyncio
async def test_guided_login_requires_explicit_selection():
    SessionBrowser.logins = 0
    first = ParallelCrawler(factory, CrawlBudget(max_pages=30, max_depth=8), [],
        login_url="https://sample.test/login", authenticate=True, discovery_mode="guided")
    await first.crawl("https://sample.test/login", record)
    assert SessionBrowser.logins == 0
    checkpoint = first._checkpoint_payload()
    login = next(job for job in checkpoint["jobs"]
        if job["path"] and job["path"][-1]["role"] == "authentication")
    second = ParallelCrawler(factory, CrawlBudget(max_pages=30, max_depth=8), [],
        login_url="https://sample.test/login", authenticate=True, discovery_mode="guided",
        checkpoint=checkpoint, selected_branches=[login["key"]])
    await second.crawl("https://sample.test/login", record)
    urls = {node["url_pattern"] for node in second.coverage["app_flow_graph"]["nodes"]}
    assert "/dashboard" in urls
    assert "/dashboard/users" not in urls
    assert SessionBrowser.logins == 1


def test_login_form_is_not_misclassified_by_forgot_password_button():
    crawler = ParallelCrawler(factory, CrawlBudget(), [], login_url=None,
        authenticate=True, discovery_mode="guided")
    nodes = [
        {"role": "RootWebArea", "name": "Login", "url": "https://cep.example/en/login"},
        {"role": "textbox", "name": "Username", "input_type": "text"},
        {"role": "textbox", "name": "Password", "input_type": "password"},
        {"role": "textbox", "name": "Enter Captcha", "input_type": "text"},
        {"role": "button", "name": "Forgot Password?", "input_type": "button"},
        {"role": "button", "name": "Login", "input_type": "submit"},
    ]
    flow = crawler._detect_auth_flow(
        "abcdef1234567890", {"url_pattern": "/en/login"}, nodes, False
    )
    assert flow is not None
    assert flow["kind"] == "login"


@pytest.mark.asyncio
async def test_guided_rejects_unknown_branch_without_opening_browser():
    crawler = ParallelCrawler(factory, CrawlBudget(), [], login_url=None,
        authenticate=False, discovery_mode="guided", selected_branches=["invented"])
    with pytest.raises(ValueError, match="not found"):
        await crawler.crawl("https://sample.test/", record)


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
