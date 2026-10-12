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
async def test_guided_round_trip_inspects_selected_role_then_authenticated_pages():
    from tests.unit.agents.test_discovery_crawler import FakeBrowser, TextBlock
    from apps.api.routers.v1.application_maps import _input_navigation_job

    class Browser(FakeBrowser):
        async def take_snapshot(self):
            if self.page == "dashboard":
                return [TextBlock('uid=2_0 RootWebArea "Dashboard" url="https://portal.test/dashboard"\nuid=2_1 button "Logout"')]
            return [TextBlock('uid=1_0 RootWebArea "Login" url="https://portal.test/login"\n'
                'uid=1_1 tab "Member"\nuid=1_2 tab "Supplier"\n'
                'uid=1_3 textbox "Member ID"\nuid=1_4 textbox "Password"\nuid=1_5 button "Sign in"')]
        async def authenticate(self):
            self.page = "dashboard"

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
