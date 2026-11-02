import asyncio

import pytest

from core.agents.application_discovery.crawler import ClickStep, CrawlBudget
from core.agents.application_discovery.parallel_crawler import ParallelCrawler
from core.tool_gateway.snapshot import parse_elements


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_feedback_does_not_expand_landing_navigation_but_keeps_local_actions(legacy):
    crawler = ParallelCrawler(None, CrawlBudget(), [], None, False, discovery_mode="guided")
    queue = asyncio.PriorityQueue()
    shared = [
        {"role": "link", "name": "Contact", "url": "/en/contact", "locator_scope": "header"},
        {"role": "button", "name": "Accessibility Options", "locator_scope": "header"},
        {"role": "button", "name": "Language", "locator_scope": "nav"},
        {"role": "link", "name": "Privacy", "url": "/en/privacy", "locator_scope": "footer"},
    ]
    async def expand(nodes, path, url):
        await crawler._enqueue_children(queue, [{"role": "RootWebArea", "url": url}, *nodes],
                                        path, True, url, "https://site.test", "public", None)
    await expand(shared, [], "https://site.test/en")
    if legacy:
        crawler._navigation_catalog = []
    path = [ClickStep("link", "Feedback", "https://site.test/en/feedback")]
    crawler._job_states["legacy-contact"] = {
        "path": [crawler._serialize_step(path[0]),
                 {"role": "link", "name": "Contact", "url": "https://site.test/en/contact"}],
        "skip_auth": True, "status": "available",
    }
    await expand(shared + [
        {"role": "button", "name": "Preview feedback", "locator_scope": "main"},
        {"role": "link", "name": "Contact", "url": "/en/feedback/help", "locator_scope": "main"},
    ], path, "https://site.test/en/feedback")
    children = [job["path"][-1]["name"] for job in crawler._job_states.values() if len(job["path"]) == 2]
    assert children == ["Preview feedback", "Contact"]
    assert "legacy-contact" not in crawler._job_states
    assert any(job["path"][-1]["name"] == "Language" for job in crawler._job_states.values())
    resumed = ParallelCrawler(None, CrawlBudget(), [], None, False, checkpoint=crawler._checkpoint_payload())
    assert resumed._navigation_catalog == crawler._navigation_catalog


def test_snapshot_landmarks_do_not_leak_into_main_content():
    nodes = parse_elements('  uid=1 banner ""\n    uid=2 button "Language"\n  uid=3 main ""\n    uid=4 button "Preview"')
    assert nodes[1]["landmark"] == "banner"
    assert nodes[3]["landmark"] == "main"


def test_main_buttons_and_different_auth_context_are_preserved():
    from core.agents.application_discovery.shared_navigation import (
        inherited_navigation,
        navigation_identity,
    )

    assert navigation_identity({"role": "button", "name": "Language", "locator_scope": "main"}, "https://site.test") is None
    assert navigation_identity({"role": "button", "name": "Preview"}, "https://site.test") is None
    assert inherited_navigation([{"path": [], "skip_auth": True, "actions": ["route:https://site.test/contact"]}],
                                [{"role": "authentication", "name": "Sign in"}], False) == set()


def test_observed_form_is_saved_on_job_without_values_and_survives_resume():
    from core.agents.application_discovery.form_inputs import observed_page_form
    from core.agents.application_discovery.parallel_crawler import DiscoveryJob

    form = observed_page_form([
        {"role": "searchbox", "name": "Search", "locator_scope": "header"},
        {"role": "textbox", "name": "Name", "required": True, "value": "private-name"},
        {"role": "textbox", "name": "Message", "value": "private-message"},
        {"role": "button", "input_type": "file", "name": "Attachment"},
        {"role": "button", "input_type": "submit", "name": "Send feedback"},
    ])
    assert [field["name"] for field in form["fields"]] == ["Name", "Message", "Attachment"]
    assert form["actions"] == [{"role": "button", "name": "Send feedback"}]
    assert "private-" not in str(form)
    crawler = ParallelCrawler(None, CrawlBudget(), [], None, False)
    job = DiscoveryJob(0, 0, observed_form=form)
    stored = crawler._serialize_job(job, "completed")
    assert crawler._deserialize_job(stored).observed_form == form
