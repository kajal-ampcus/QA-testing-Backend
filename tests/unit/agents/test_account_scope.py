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


def test_employee_admin_kitchen_round_trip_preserves_frontiers_and_combined_graph():
    graph = {"nodes": [{"fingerprint": "employee-home"}], "edges": []}
    employee = {
        "jobs": [{"key": "employee", "status": "completed"}],
        "completed_nodes": ["employee-home"],
        "progress": {"completed_tasks": 1},
        "pending_nodes": [], "failed_nodes": [], "in_progress_nodes": [],
        "live_view": {"label": "Employee"},
        "credential_update_required": True,
    }
    original = {"configuration": {"credential_ref": "cred:employee"}, "graph": graph, **employee}
    snapshot = deepcopy(original)
    admin = select_account_checkpoint(original, "cred:admin")
    assert original == snapshot
    assert "progress" not in admin
    assert "live_view" not in admin
    assert "credential_update_required" not in admin
    admin.update(jobs=[{"key": "admin", "status": "failed"}], progress={"failed_tasks": 1})
    admin["graph"]["nodes"].append({"fingerprint": "admin-home"})
    admin["graph"]["edges"].append({"parent": "employee-home", "child": "admin-home", "route": "admin"})
    kitchen = select_account_checkpoint(admin, "cred:kitchen")
    kitchen.update(jobs=[{"key": "kitchen", "status": "available"}])
    kitchen["graph"]["nodes"].append({"fingerprint": "kitchen-home"})
    restored = select_account_checkpoint(kitchen, "cred:employee")
    for key, value in employee.items():
        assert restored[key] == value
    assert restored["account_contexts"]["cred:admin"]["jobs"][0]["status"] == "failed"
    assert restored["account_contexts"]["cred:kitchen"]["jobs"][0]["key"] == "kitchen"
    assert len(restored["graph"]["nodes"]) == 3
    assert restored["graph"]["edges"] == admin["graph"]["edges"]
    assert len(original["graph"]["nodes"]) == 1
