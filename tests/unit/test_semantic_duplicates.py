from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from domain.semantic_duplicates import find_semantic_duplicate
from domain.semantic_duplicates import comparison_batches
import json


@pytest.mark.asyncio
async def test_exact_title_ignores_punctuation_without_model():
    client = SimpleNamespace(call_structured=AsyncMock())
    assert await find_semantic_duplicate({"title": "LOGIN: empty password!"}, [{"id": "1", "title": "Login empty password"}], client) == "1"
    client.call_structured.assert_not_awaited()


@pytest.mark.asyncio
async def test_paraphrase_uses_structured_verdict():
    client = SimpleNamespace(call_structured=AsyncMock(return_value=SimpleNamespace(data={"existing_id": "1", "reason": "Both verify blank password rejection"})))
    assert await find_semantic_duplicate({"title": "Sign in with blank password"}, [{"id": "1", "title": "Login with an empty password"}], client) == "1"


@pytest.mark.asyncio
async def test_distinct_scenario_is_allowed():
    client = SimpleNamespace(call_structured=AsyncMock(return_value=SimpleNamespace(data={"existing_id": None, "reason": "Empty username differs from empty password"})))
    assert await find_semantic_duplicate({"title": "Empty username"}, [{"id": "1", "title": "Empty password"}], client) is None


@pytest.mark.asyncio
async def test_unknown_model_id_cannot_suppress_case():
    client = SimpleNamespace(call_structured=AsyncMock(return_value=SimpleNamespace(data={"existing_id": "invented", "reason": "match"})))
    with pytest.raises(ValueError):
        await find_semantic_duplicate({"title": "New case"}, [{"id": "1", "title": "Saved case"}], client)


@pytest.mark.asyncio
async def test_provider_failure_does_not_silently_allow_case():
    client = SimpleNamespace(call_structured=AsyncMock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError):
        await find_semantic_duplicate({"title": "New case"}, [{"id": "1", "title": "Saved case"}], client)


def test_long_cases_are_split_by_actual_utf8_size_without_losing_rows():
    candidate = {"title": "A new scenario"}
    rows = [{"id": str(i), "title": f"Case {i}", "steps": [{"expected": "界" * 400}]} for i in range(25)]
    batches = list(comparison_batches(candidate, rows))
    assert len(batches) > 1
    assert [row for batch in batches for row in batch] == rows
    assert all(len(json.dumps({"candidate": candidate, "existing": batch}, ensure_ascii=False).encode("utf-8")) <= 5000 for batch in batches)


@pytest.mark.asyncio
async def test_oversized_single_case_rejected_before_any_model_call():
    client = SimpleNamespace(call_structured=AsyncMock())
    with pytest.raises(ValueError, match="size budget"):
        await find_semantic_duplicate({"title": "New scenario"}, [{"id": "1", "title": "Saved", "objective": "x" * 9000}], client)
    client.call_structured.assert_not_awaited()


@pytest.mark.asyncio
async def test_production_matching_never_initializes_provider(monkeypatch):
    from infra.llm import factory
    def forbidden(*args, **kwargs):
        raise AssertionError("Production duplicate check must not call Groq")
    monkeypatch.setattr(factory, "get_llm_client", forbidden)
    rows = [{"id": str(i), "title": f"Unrelated scenario {i}", "steps": "x" * 20000} for i in range(200)]
    rows.append({"id": "match", "title": "Login with an empty password"})
    assert await find_semantic_duplicate({"title": "Check sign in with a blank password"}, rows) == "match"
    assert await find_semantic_duplicate({"title": "Login with an invalid password"}, rows) is None


@pytest.mark.asyncio
async def test_local_matching_keeps_inputs_negation_and_sequence_distinct():
    existing = [{"id": "1", "title": "Login with an empty password"}]
    for title in ["Login with an empty username", "Login with an invalid password", "Do not login with an empty password"]:
        assert await find_semantic_duplicate({"title": title}, existing) is None
    assert await find_semantic_duplicate({"title": "Logout then login"}, [{"id": "1", "title": "Login then logout"}]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate,existing", [
    ({"title": "Verify login validation when username is blank"}, {"title": "Login with an empty username"}),
    ({"title": "1. Enter valid username", "expected_result": "User is authenticated and navigated to the dashboard page."},
     {"title": "Verify successful login with valid credentials", "expected_result": "Valid credentials authenticate the user."}),
    ({"title": "1. Enter a password", "expected_result": "Password characters are masked by default."},
     {"title": "Verify password is masked by default", "expected_result": "Password input uses type=password"}),
    ({"title": "1. Enter a valid username", "expected_result": "Username is accepted and displayed."},
     {"title": "Verify username accepts valid input", "expected_result": "Username accepts a valid username"}),
    ({"title": "1. Click Back to Home", "expected_result": "User is navigated to the home page."},
     {"title": "Verify Back to Home navigation", "expected_result": "Home page is visible"}),
])
async def test_cross_source_paraphrases_share_behavior(candidate, existing):
    assert await find_semantic_duplicate(candidate, [{"id": "saved", **existing}]) == "saved"


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate,existing", [
    ({"title": "Login", "expected_result": "Dashboard opens"}, {"title": "Login", "expected_result": "Error appears"}),
    ({"title": "Password boundary of 8 characters"}, {"title": "Password boundary of 64 characters"}),
    ({"title": "Login with blank email"}, {"title": "Login with blank password"}),
    ({"title": "Login with valid credentials", "expected_result": "User is authenticated", "steps": [{"expected": "Press Enter using keyboard"}]},
     {"title": "Successful login with valid credentials", "expected_result": "User is authenticated"}),
    ({"title": "1. Enter valid credentials", "expected_result": "Password is not exposed in the URL"},
     {"title": "1. Enter valid credentials", "expected_result": "Login is submitted and user navigated to dashboard"}),
    ({"title": "Admin successful login"}, {"title": "Candidate successful login"}),
    ({"title": "Verify password accepts valid input", "expected_result": "Password accepts input"},
     {"title": "Enter a valid password", "expected_result": "Password accepts input and keeps it masked"}),
    ({"title": "Login UI", "expected_result": "Controls are displayed with correct alignment"},
     {"title": "Login UI", "expected_result": "Controls are displayed"}),
    ({"title": "Field boundary", "test_data": {"value": 8}}, {"title": "Field boundary", "test_data": {"value": 9}}),
])
async def test_meaningful_conditions_and_outcomes_are_not_duplicates(candidate, existing):
    assert await find_semantic_duplicate(candidate, [{"id": "saved", **existing}]) is None
