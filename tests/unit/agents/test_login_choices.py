import asyncio
from contextlib import asynccontextmanager

import pytest

from core.agents.application_discovery.login_choices import login_choices, login_group_key
from core.agents.application_discovery.form_inputs import blocking_form
from core.agents.application_discovery.parallel_crawler import ParallelCrawler, DiscoveryJob
from core.agents.application_discovery.crawler import CrawlBudget, ClickStep


def login_nodes():
    return [
        {"role": "textbox", "name": "Member ID"},
        {"role": "textbox", "name": "Password", "input_type": "password"},
        {"role": "button", "name": "Sign in"},
        *[{"role": "button", "name": role, "control_group": "/main/form/div"}
          for role in ["Member", "Supplier", "Auditor"]],
        {"role": "button", "name": "Show password", "control_group": "/main/form/div"},
    ]


def test_observed_roles_have_no_application_specific_vocabulary():
    assert [c["label"] for c in login_choices(login_nodes(), "https://portal.test/login")] == ["Member", "Supplier", "Auditor"]
    assert blocking_form(login_nodes())["kind"] == "login"
    assert len(blocking_form(login_nodes())["fields"]) == 2


def test_separate_routes_and_dropdown_choices():
    routes = [{"role": "link", "name": f"{name} login", "url": f"/{name}"} for name in ["buyer", "seller"]]
    assert len(login_choices(routes, "https://portal.test")) == 2
    nodes = login_nodes()[:3] + [{"role": "combobox", "name": "Account type", "options": ["Buyer", "Seller"]}]
    assert [c["step"]["value"] for c in login_choices(nodes, "https://portal.test")] == ["Buyer", "Seller"]
    assert login_group_key("https://a.test/login", "Buyer") != login_group_key("https://b.test/login", "Buyer")


def test_unrelated_and_external_controls_are_not_login_choices():
    nodes = login_nodes()[:3] + [
        {"role": "button", "name": "Help"},
        {"role": "link", "name": "Vendor login", "url": "https://other.test/login"},
    ]
    assert login_choices(nodes, "https://portal.test/login") == []


@pytest.mark.asyncio
async def test_discovery_requests_role_before_credentials_and_keeps_saved_account_path():
    crawler = ParallelCrawler(client_factory=None, budget=CrawlBudget(), keywords=[], login_url=None, authenticate=False, discovery_mode="guided")
    queue = asyncio.PriorityQueue()
    job = DiscoveryJob(0, 0, skip_auth=True)
    await crawler._consider_form(queue, login_nodes(), job, True, "https://portal.test/login", "/login", "Login", None)
    request = next(iter(crawler._input_requests.values()))
    assert request["kind"] == "login_choice"
    assert not request["fields"]
    choice = request["choices"][1]
    job.path = [ClickStep(**choice["step"])]
    request.update(status="applied", selected_choice=choice, selected_path=[crawler._serialize_step(step) for step in job.path])
    await crawler._consider_form(queue, login_nodes(), job, True, "https://portal.test/login", "/login", "Login", None)
    form = list(crawler._input_requests.values())[-1]
    assert form["kind"] == "login"
    assert form["login_label"] == "Supplier"
    assert form["login_group"] == choice["id"]
    saved = ParallelCrawler(client_factory=None, budget=CrawlBudget(), keywords=[], login_url=None, authenticate=True, credential_ref="cred:existing")
    await saved._consider_form(queue, login_nodes(), job, True, "https://portal.test/login", "/login", "Login", None)
    assert not saved._input_requests


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_sidebar", [False, True])
@pytest.mark.parametrize("explore_all", [False, True])
async def test_guided_round_trip_inspects_selected_role_then_authenticated_pages(custom_sidebar, explore_all):
    from tests.unit.agents.test_discovery_crawler import FakeBrowser, TextBlock
    from apps.api.routers.v1.application_maps import _input_navigation_job

    class Browser(FakeBrowser):
        async def take_snapshot(self):
            if self.page == "dashboard":
                return [TextBlock('uid=2_0 RootWebArea "Dashboard" url="https://portal.test/dashboard"\nuid=2_1 button "Logout"'
                    + ('\nuid=2_2 StaticText "Users"\nuid=2_3 StaticText "Announcements"\nuid=2_4 StaticText "Platform management"' if custom_sidebar else ''))]
            if self.page == "users":
                return [TextBlock('uid=3_0 RootWebArea "Users" url="https://portal.test/users"\nuid=3_1 heading "Users"')]
            if self.page == "announcements":
                return [TextBlock('uid=4_0 RootWebArea "Announcements" url="https://portal.test/announcements"\nuid=4_1 heading "Announcements"')]
            return [TextBlock('uid=1_0 RootWebArea "Login" url="https://portal.test/login"\n'
                'uid=1_1 tab "Member"\nuid=1_2 tab "Supplier"\n'
                'uid=1_3 textbox "Member ID"\nuid=1_4 textbox "Password"\nuid=1_5 button "Sign in"')]
        async def authenticate(self):
            self.page = "dashboard"
        async def inspect_elements(self, snapshot):
            from core.tool_gateway.snapshot import parse_elements
            nodes = parse_elements(snapshot)
            if self.page == "dashboard":
                if custom_sidebar:
                    for node in nodes:
                        if node.get("name") in {"Users", "Announcements"}:
                            node.update(navigation_clickable=True, locator_scope="aside")
                else:
                    nodes.append({"uid": "2_2", "role": "link", "name": "Users", "url": "https://portal.test/users"})
            return nodes
        async def click(self, uid):
            if self.page == "dashboard" and uid == "2_2":
                self.page = "users"
            elif self.page == "dashboard" and uid == "2_3":
                self.page = "announcements"
            else:
                await super().click(uid)
        async def navigate_page(self, url):
            self.page = "users" if url.endswith("/users") else "login"

    @asynccontextmanager
    async def browser():
        yield Browser()

    states = []
    async def record(state): states.append(state)
    first = ParallelCrawler(browser, CrawlBudget(max_pages=20), [], login_url=None, authenticate=False, discovery_mode="guided", worker_limit=1)
    await first.crawl("https://portal.test/login", record)
    assert first.termination_reason == "WAITING_FOR_INPUT"
    checkpoint = first._checkpoint_payload()
    choice_request = checkpoint["input_requests"][0]
    choice = choice_request["choices"][1]
    raw = _input_navigation_job(checkpoint, [choice["step"]])
    choice_request.update(status="applied", selected_choice=choice, selected_path=raw["path"])
    second = ParallelCrawler(browser, CrawlBudget(max_pages=20), [], login_url=None, authenticate=False,
        discovery_mode="guided", worker_limit=1, checkpoint=checkpoint, selected_branches=[raw["key"]])
    await second.crawl("https://portal.test/login", record)
    login = next(item for item in second._input_requests.values() if item["kind"] == "login")
    assert login["login_label"] == "Supplier"
    assert second.termination_reason == "WAITING_FOR_INPUT"
    checkpoint = second._checkpoint_payload()
    login["status"] = "approved"
    raw = _input_navigation_job(checkpoint, [*login["navigation_path"], {"role": "authentication", "name": "Sign in with selected account"}])
    third = ParallelCrawler(browser, CrawlBudget(max_pages=20), [], login_url=None, authenticate=True,
        credential_ref="cred:supplier", discovery_mode="guided", worker_limit=1, checkpoint=checkpoint, selected_branches=[raw["key"]])
    await third.crawl("https://portal.test/login", record)
    assert any(state["url_pattern"] == "/dashboard" for state in states)
    checkpoint = third._checkpoint_payload()
    users = next(job for job in checkpoint["jobs"] if job["path"]
        and job["path"][-1].get("name") == "Users")
    assert users["status"] == "available"
    assert not any(state["url_pattern"] == "/users" for state in states)
    assert third.termination_reason == "AWAITING_BRANCH_SELECTION"
    dashboard = next(node for node in checkpoint["graph"]["nodes"] if node["url_pattern"] == "/dashboard")
    assert users["parent_fingerprint"] == dashboard["fingerprint"]
    if custom_sidebar:
        assert any(job["path"][-1].get("name") == "Announcements" and job["status"] == "available"
                   for job in checkpoint["jobs"] if job["path"])
        assert not any(job["path"][-1].get("name") == "Platform management"
                       for job in checkpoint["jobs"] if job["path"])
    fourth = ParallelCrawler(browser, CrawlBudget(max_pages=20), [], login_url=None, authenticate=True,
        credential_ref="cred:supplier", discovery_mode="guided", worker_limit=1,
        checkpoint=checkpoint, selected_branches=[job["key"] for job in checkpoint["jobs"]
            if job["status"] == "available"] if explore_all else [users["key"]])
    await fourth.crawl("https://portal.test/login", record)
    assert any(state["url_pattern"] == "/users" for state in states)
    assert any(state["url_pattern"] == "/announcements" for state in states) == (custom_sidebar and explore_all)
    assert any(edge["parent_fingerprint"] == dashboard["fingerprint"]
               and edge["child_fingerprint"] == next(node["fingerprint"] for node in fourth.coverage["app_flow_graph"]["nodes"] if node["url_pattern"] == "/users")
               for edge in fourth.coverage["app_flow_graph"]["edges"])
