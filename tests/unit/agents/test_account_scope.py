from copy import deepcopy

import pytest

from core.agents.application_discovery.account_scope import select_account_checkpoint
from core.agents.application_discovery.crawler import CrawlBudget
from core.agents.application_discovery.parallel_crawler import ParallelCrawler
from tests.unit.agents.test_discovery_modes import factory, record


@pytest.mark.asyncio
async def test_multiple_accounts_keep_independent_progress_and_shared_results():
    checkpoint = None

    async def run(account, selected=()):
        nonlocal checkpoint
        scoped = select_account_checkpoint(checkpoint, account) if checkpoint else None
        crawler = ParallelCrawler(factory, CrawlBudget(max_pages=50, max_depth=8), [],
            login_url="https://sample.test/login", authenticate=True, credential_ref=account,
            discovery_mode="guided", checkpoint=scoped, selected_branches=list(selected))
        await crawler.crawl("https://sample.test/login", record)
        checkpoint = crawler._checkpoint_payload()
        checkpoint["configuration"]["credential_ref"] = account
        return crawler

    def sign_in():
        return next(job["key"] for job in checkpoint["jobs"] if job["path"]
            and job["path"][-1]["name"] == "Sign in with selected account")

    await run("cred:employee")
    await run("cred:employee", [sign_in()])
    employee_jobs = deepcopy(checkpoint["jobs"])
    employee_nodes = {node["fingerprint"] for node in checkpoint["graph"]["nodes"]}
    await run("cred:admin")
    assert employee_nodes <= {node["fingerprint"] for node in checkpoint["graph"]["nodes"]}
    await run("cred:admin", [sign_in()])
    # Identical dashboard HTML and URL still represent different account observations.
    dashboards = [node for node in checkpoint["graph"]["nodes"] if node["url_pattern"] == "/dashboard"]
    assert len(dashboards) == 2
    await run("cred:kitchen")
    await run("cred:kitchen", [sign_in()])
    assert len([node for node in checkpoint["graph"]["nodes"] if node["url_pattern"] == "/dashboard"]) == 3
    await run("cred:employee")
    assert checkpoint["jobs"] == employee_jobs
    assert len([node for node in checkpoint["graph"]["nodes"] if node["url_pattern"] == "/dashboard"]) == 3
