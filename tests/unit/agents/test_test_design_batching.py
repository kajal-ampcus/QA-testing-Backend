"""Compact, concurrent test-design batches must stay small and overlap."""

import asyncio

import pytest

from core.agents.test_design.agent import (
    _chunked,
    _env_float,
    _env_int,
    _generation_jobs,
    _is_missing_model_error,
)
from core.agents.test_design.prompts import build_user_prompt
from core.agents.test_design.schemas import TestCaseBatch as CaseBatch
from core.agents.test_design.schemas import TestCaseSpec as CaseSpec


def test_env_int_reads_positive_overrides(monkeypatch) -> None:
    monkeypatch.delenv("TEST_DESIGN_MAX_TOKENS", raising=False)
    assert _env_int("TEST_DESIGN_MAX_TOKENS", 3200) == 3200
    monkeypatch.setenv("TEST_DESIGN_MAX_TOKENS", "1200")
    assert _env_int("TEST_DESIGN_MAX_TOKENS", 3200) == 1200
    monkeypatch.setenv("TEST_DESIGN_MAX_TOKENS", "0")
    assert _env_int("TEST_DESIGN_MAX_TOKENS", 3200) == 1


def test_env_float_reads_timeout_override(monkeypatch) -> None:
    monkeypatch.setenv("TEST_DESIGN_LLM_TIMEOUT_SECONDS", "45")
    assert _env_float("TEST_DESIGN_LLM_TIMEOUT_SECONDS", 120.0) == 45.0


def test_chunked_groups_acceptance_criteria() -> None:
    assert _chunked(["a", "b", "c", "d", "e"], 2) == [["a", "b"], ["c", "d"], ["e"]]
    assert _chunked([], 4) == []
    assert _chunked(["only"], 4) == [["only"]]


def test_missing_groq_model_is_detected() -> None:
    err = Exception(
        "Error code: 404 - {'error': {'message': 'The model `llama-3.1-8b-instant` "
        "does not exist or you do not have access to it.', 'code': 'model_not_found'}}"
    )
    assert _is_missing_model_error(err)
    assert not _is_missing_model_error(Exception("rate limit 429"))


def test_batch_schema_caps_payload_size() -> None:
    schema = CaseBatch.model_json_schema()
    test_cases = schema["properties"]["test_cases"]
    assert test_cases["maxItems"] == 4
    steps = CaseSpec.model_json_schema()["properties"]["steps"]
    assert steps["maxItems"] == 8


def test_prompt_asks_for_compact_batches() -> None:
    prompt = build_user_prompt(
        requirement_title="Login",
        requirement_description="Users can sign in",
        acceptance_criteria=[
            {"id": "AC-1", "text": "Valid credentials succeed"},
            {"id": "AC-2", "text": "Invalid credentials are rejected"},
        ],
        app_map_states=[],
        base_url="https://example.test",
    )
    lowered = prompt.lower()
    assert "for each acceptance criterion" in lowered
    assert "2 acs → 4 cases" in lowered
    targeted = build_user_prompt(
        requirement_title="Login",
        requirement_description="Users can sign in",
        acceptance_criteria=[{"id": "AC-1", "text": "Valid credentials succeed"}],
        app_map_states=[],
        base_url="https://example.test",
        targeted_by_ac={"AC-1": ["POSITIVE"]},
    )
    assert "targeted coverage" in targeted.lower()
    assert "exactly 1 compact test case" in targeted.lower()


def test_generation_jobs_split_positive_then_negative() -> None:
    acs = [{"id": "AC-1"}, {"id": "AC-2"}, {"id": "AC-3"}]
    jobs = _generation_jobs(acs, {}, 2)
    assert [list(targeted.values())[0] for _chunk, targeted in jobs] == [
        ["POSITIVE"],
        ["POSITIVE"],
        ["NEGATIVE"],
        ["NEGATIVE"],
    ]
    assert [ac["id"] for ac in jobs[0][0]] == ["AC-1", "AC-2"]
    assert [ac["id"] for ac in jobs[1][0]] == ["AC-3"]


@pytest.mark.asyncio
async def test_timeout_starts_after_concurrency_slot() -> None:
    """Queued chunks must not share one timeout window or they all 502 together."""
    slot = asyncio.Semaphore(1)
    outcomes: list[str] = []

    async def generate() -> None:
        async with slot:
            try:
                await asyncio.wait_for(asyncio.sleep(0.12), timeout=0.2)
                outcomes.append("ok")
            except TimeoutError:
                outcomes.append("timeout")

    await asyncio.gather(*(generate() for _ in range(3)))
    assert outcomes == ["ok", "ok", "ok"]
