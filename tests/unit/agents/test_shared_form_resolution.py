"""Regression coverage for scoped lookup, live filling and approved replay."""

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from core.agents.application_discovery.form_inputs import describe_field, discovery_form
from core.tool_gateway.form_resolution import (
    FormContext,
    account_candidates,
    apply_values,
    resolve_candidates,
    resolve_form,
)


def control(name="Email", **kwargs):
    return {"uid": "1", "role": "textbox", "name": name, "required": True, **kwargs}


def test_conflicts_invalid_types_and_duplicate_labels_require_input():
    email = control(input_type="email")
    assert resolve_candidates(
        [email], {"textbox:email": ["a@example.test", "b@example.test"]}
    ).missing
    assert resolve_candidates([email], {"textbox:email": ["invalid"]}).missing
    assert (
        resolve_candidates(
            [email, dict(email, uid="2")], {"textbox:email": ["a@example.test"]}
        ).values
        == {}
    )
    resolved = resolve_candidates([email], {"textbox:email": ["a@example.test", "a@example.test"]})
    assert resolved.values == {"textbox:email": "a@example.test"}
    assert not resolved.missing


def test_optional_preserved_and_ephemeral_inputs():
    nodes = [
        control("Nickname", required=False),
        control("Existing", has_value=True),
        control("OTP"),
        control("Session token"),
        control("CAPTCHA"),
    ]
    values = {describe_field(node)["key"]: ["saved"] for node in nodes}
    resolved = resolve_candidates(nodes, values)
    assert resolved.values == {"textbox:nickname": "saved"}
    assert {field["name"] for field in resolved.missing} == {"OTP", "Session token", "CAPTCHA"}
    assert not resolve_candidates([control("Optional", required=False)], {}).missing
    assert not resolve_candidates([control(user_modified=True, value="")], {}).values


def test_account_matching_is_exact_and_detects_internal_disagreement():
    nodes = [control("Email"), control("Backup email"), control("Tenant")]
    secret = {
        "username": "a@example.test",
        "fields": [{"name": "Tenant", "value": "one"}, {"name": "Tenant", "value": "two"}],
    }
    result = resolve_candidates(nodes, account_candidates(secret, nodes))
    assert result.values == {"textbox:email": "a@example.test"}
    assert result.conflicts == ["textbox:tenant"]
    assert {field["name"] for field in result.missing} == {"Backup email", "Tenant"}


@pytest.mark.parametrize(
    "node,value,expected",
    [
        (control("Country", role="combobox", options=["India", "Japan"]), "India", "India"),
        (control("Country", role="combobox", options=["India"]), "india", None),
        (
            control(
                "Country",
                role="combobox",
                option_items=[{"label": "India", "value": "IN", "enabled": False}],
            ),
            "IN",
            None,
        ),
        (
            control("Country", role="combobox", option_items=[{"label": "India", "value": "IN"}]),
            "India",
            "IN",
        ),
        (control("Accept", role="checkbox"), "true", "true"),
        (control("Accept", role="checkbox"), "anything", None),
        (control("Choice", role="radio"), "true", "true"),
        (control("Age", role="spinbutton", min="1", max="10"), "11", None),
    ],
)
def test_controls_validate_live_options_and_types(node, value, expected):
    key = describe_field(node)["key"]
    assert resolve_candidates([node], {key: [value]}).values.get(key) == expected


@pytest.mark.asyncio
async def test_repository_lookup_is_scoped_and_sources_do_not_silently_override(monkeypatch):
    answer = AsyncMock(return_value={"textbox:email": "answer@example.test"})
    vault = AsyncMock(
        return_value={"username": "account@example.test", "login_url": "https://site.test/login"}
    )
    fixture = AsyncMock(return_value={})
    monkeypatch.setattr("infra.db.repositories.form_answer_repo.get_form_answers", answer)
    monkeypatch.setattr("core.tool_gateway.secret_resolver.resolve_login", vault)
    monkeypatch.setattr("infra.db.repositories.form_test_data_repo.discovery_fixture", fixture)
    context = FormContext(
        uuid.uuid4(),
        "/login",
        "form-one",
        "cred:one",
        login=True,
        page_url="https://site.test/login",
    )
    result = await resolve_form(context, [control()])
    assert not result.values and result.conflicts == ["textbox:email"]
    answer.assert_awaited_once_with(
        context.project_id, "/login", "form-one", "cred:one", login=True
    )
    vault.assert_awaited_once_with("cred:one", context.project_id)
    fixture.assert_awaited_once_with(context)
    vault.reset_mock()
    await resolve_form(
        FormContext(context.project_id, "/contact", "other", "cred:one"), [control()]
    )
    vault.assert_not_awaited()  # Never copy a login identity into a business form.


@pytest.mark.asyncio
async def test_account_on_another_login_route_is_ineligible(monkeypatch):
    monkeypatch.setattr(
        "infra.db.repositories.form_answer_repo.get_form_answers", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        "core.tool_gateway.secret_resolver.resolve_login",
        AsyncMock(return_value={"username": "private", "login_url": "https://site.test/admin"}),
    )
    result = await resolve_form(
        FormContext(
            uuid.uuid4(),
            "/customer",
            "f",
            "cred:a",
            login=True,
            page_url="https://site.test/customer",
        ),
        [control()],
    )
    assert not result.values and result.missing


@pytest.mark.asyncio
async def test_unassigned_fixture_context_never_opens_a_database(monkeypatch):
    from infra.db.repositories.form_test_data_repo import discovery_fixture

    def forbidden():
        raise AssertionError("An ordinary crawl must not scan fixture rows")

    monkeypatch.setattr("infra.db.repositories.form_test_data_repo.AsyncSessionLocal", forbidden)
    assert await discovery_fixture(FormContext(uuid.uuid4(), "/contact", "form")) == {}


class Browser:
    def __init__(self, nodes):
        self.nodes = nodes
        self.fills = []
        self.clicks = []

    async def take_snapshot(self):
        return {"elements": self.nodes}

    async def fill(self, uid, value):
        self.fills.append((uid, value))
        next(node for node in self.nodes if node["uid"] == uid)["has_value"] = True

    async def click(self, uid):
        self.clicks.append(uid)


@pytest.mark.asyncio
async def test_live_fill_preserves_new_values_and_never_toggles_populated_controls():
    browser = Browser(
        [
            control(has_value=True),
            control("Terms", uid="2", role="checkbox", checked=True),
            control("Opt in", uid="3", role="checkbox", checked=False),
        ]
    )
    applied = await apply_values(
        browser,
        browser.nodes,
        {"textbox:email": "saved", "checkbox:terms": "false", "checkbox:opt in": "false"},
    )
    assert len(applied) == 3
    assert browser.fills == [] and browser.clicks == []


@pytest.mark.asyncio
async def test_discovery_fills_optional_and_prompts_only_missing_without_checkpoint_values(
    monkeypatch,
):
    from core.agents.application_discovery.crawler import CrawlBudget
    from core.agents.application_discovery.parallel_crawler import DiscoveryJob, ParallelCrawler

    nodes = [
        control("Name"),
        control("City", uid="2"),
        control("Nickname", uid="3", required=False),
        {"role": "button", "name": "Submit", "uid": "4"},
    ]
    answer = AsyncMock(
        return_value={"textbox:name": "private-name", "textbox:nickname": "private-nick"}
    )
    monkeypatch.setattr("infra.db.repositories.form_answer_repo.get_form_answers", answer)
    crawler = ParallelCrawler(
        None, CrawlBudget(), [], None, False, project_id=uuid.uuid4(), credential_ref="cred:a"
    )
    browser = Browser(nodes)
    await crawler._consider_form(
        asyncio.PriorityQueue(),
        nodes,
        DiscoveryJob(0, 0),
        False,
        "https://site.test/contact",
        "/contact",
        "Contact",
        None,
        browser,
    )
    request = next(iter(crawler._input_requests.values()))
    assert [field["name"] for field in request["fields"]] == ["City"]
    assert request["credential_ref"] == "cred:a"
    assert browser.fills == [("1", "private-name"), ("3", "private-nick")]
    assert not browser.clicks
    assert "private-" not in str(crawler._checkpoint_payload())
    # Approval loads both the explicit missing input and the resolved companions.
    answer.return_value = {
        "textbox:name": "private-name",
        "textbox:nickname": "private-nick",
        "textbox:city": "City",
    }
    request["status"] = "approved"
    resolved = await crawler._request_answers(request)
    job = crawler._fill_job(request, resolved)
    await crawler._hydrate_job(job)
    assert {step.field_key for step in job.path if step.sensitive} == set(resolved)
    assert "private-" not in str(crawler._serialize_job(job, "pending"))


def test_optional_controls_keep_the_existing_required_form_key():
    first = discovery_form([control(), {"role": "button", "name": "Submit"}])
    second = discovery_form(
        [control(), control("Nickname", required=False), {"role": "button", "name": "Submit"}]
    )
    assert first["form_key"] == second["form_key"]


def test_radio_group_does_not_change_an_existing_choice_or_pick_two_choices():
    nodes = [
        control("One", role="radio", radio_group="plan"),
        control("Two", uid="2", role="radio", radio_group="plan"),
    ]
    assert not resolve_candidates(nodes, {"radio:one": ["true"], "radio:two": ["true"]}).values
    nodes[0]["checked"] = True
    assert not resolve_candidates(nodes, {"radio:two": ["true"]}).values


@pytest.mark.asyncio
async def test_missing_account_never_reuses_unscoped_login_answers(monkeypatch):
    lookup = AsyncMock(side_effect=AssertionError("Login needs a selected account"))
    monkeypatch.setattr("infra.db.repositories.form_answer_repo.get_form_answers", lookup)
    result = await resolve_form(
        FormContext(uuid.uuid4(), "/login", "session:f", login=True), [control()]
    )
    assert result.missing
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_preserved_input_replay_checks_presence_without_storing_its_value(monkeypatch):
    from core.agents.application_discovery.crawler import CrawlBudget, Crawler
    from core.agents.application_discovery.parallel_crawler import DiscoveryJob, ParallelCrawler

    nodes = [
        control("Name", has_value=True),
        control("City", uid="2"),
        {"uid": "3", "role": "button", "name": "Submit"},
    ]
    monkeypatch.setattr(
        "infra.db.repositories.form_answer_repo.get_form_answers", AsyncMock(return_value={})
    )
    crawler = ParallelCrawler(None, CrawlBudget(), [], None, False, project_id=uuid.uuid4())
    browser = Browser(nodes)
    await crawler._consider_form(
        asyncio.PriorityQueue(),
        nodes,
        DiscoveryJob(0, 0),
        True,
        "https://site.test/form",
        "/form",
        "Form",
        None,
        browser,
    )
    request = next(iter(crawler._input_requests.values()))
    assert [item["name"] for item in request["fields"]] == ["City"]
    job = crawler._fill_job(request, {"textbox:city": "Paris"})
    assert job.path[0].role == "preserve_input"
    replay = Crawler(browser, CrawlBudget(), [])
    await replay._apply_path(await browser.take_snapshot(), job.path[:1])
    nodes[0]["has_value"] = False
    with pytest.raises(RuntimeError, match="previously populated"):
        await replay._apply_path(await browser.take_snapshot(), job.path[:1])
    assert not browser.fills


@pytest.mark.asyncio
async def test_fixture_repository_checks_case_project_account_and_scenario(monkeypatch):
    from types import SimpleNamespace

    from infra.db.models.test_case import TestCase
    from infra.db.repositories.form_test_data_repo import discovery_fixture

    project_id, case_id = uuid.uuid4(), uuid.uuid4()
    context = FormContext(
        project_id,
        "/form",
        "f",
        "cred:a",
        test_case_id=case_id,
        scenario="positive",
        form_group="Contact",
    )
    case = SimpleNamespace(project_id=project_id, credential_ref="cred:a", id=case_id)

    class Session:
        query = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, model, identity):
            return case if model is TestCase else SimpleNamespace(credential_ref="cred:default")

        async def scalars(self, query):
            self.query = query
            return SimpleNamespace(
                all=lambda: [SimpleNamespace(test_data={"Email": "fixture@test.example"})]
            )

    session = Session()
    monkeypatch.setattr(
        "infra.db.repositories.form_test_data_repo.AsyncSessionLocal", lambda: session
    )
    assert await discovery_fixture(context) == {"Email": "fixture@test.example"}
    parameters = session.query.compile().params
    assert project_id in parameters.values() and case_id in parameters.values()
    assert "Contact" in parameters.values() and "positive" in parameters.values()
    case.credential_ref = "cred:other"
    assert await discovery_fixture(context) == {}
    case.project_id = uuid.uuid4()
    assert await discovery_fixture(context) == {}


@pytest.mark.asyncio
async def test_resolver_login_answers_move_to_vault_without_a_second_durable_copy(monkeypatch):
    from types import SimpleNamespace

    from apps.api.routers.v1 import application_maps as api

    project_id, map_id = uuid.uuid4(), uuid.uuid4()
    descriptor = describe_field(control("Tenant"))
    request = {
        "id": "input",
        "kind": "login",
        "status": "pending",
        "page_key": "/login",
        "form_key": "f",
        "credential_ref": "cred:a",
        "fields": [descriptor],
        "resolution_fields": [descriptor],
    }
    app_map = SimpleNamespace(
        id=map_id,
        project_id=project_id,
        status="PARTIAL",
        discovery_checkpoint={"configuration": {}, "input_requests": [request]},
    )
    session = SimpleNamespace(get=AsyncMock(return_value=app_map), commit=AsyncMock())
    merge, save = AsyncMock(), AsyncMock()
    monkeypatch.setattr(api, "merge_login_fields", merge)
    monkeypatch.setattr(api, "save_form_answers", save)
    await api.submit_input_request(
        map_id,
        "input",
        api.SubmitInputRequestBody(
            approve_submission=True,
            fields=[api.InputFieldAnswer(key=descriptor["key"], value="tenant-one")],
        ),
        session,
    )
    merge.assert_awaited_once_with(
        "cred:a", project_id, [{"name": "Tenant", "value": "tenant-one"}]
    )
    assert save.await_args.args[3] == {}
    assert save.await_args.kwargs["remove_values"] == {"textbox:tenant"}
    assert "tenant-one" not in str(app_map.discovery_checkpoint)
