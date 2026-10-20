import asyncio
import json
import os
import shutil
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.api.routers.v1 import executions
from core.agents.automation_generation.artifacts import build_zip
from core.agents.automation_generation.input_runtime import RUNTIME_SOURCE
from core.agents.input_resolution.coordinator import coordinate_inputs, resolve_input
from infra.execution_inputs import (
    actual_inputs,
    prepare_run_inputs,
    read_json,
    validate_answer,
    write_json,
)


def binding(**changes):
    return {
        "key": "TC-001:v1:step2",
        "case_code": "TC-001",
        "case_version": 1,
        "field": "Email",
        "category": "POSITIVE",
        "objective": "Enter a valid email",
        "negative_target": False,
        "allow_empty": False,
        "sensitive": False,
        "validation": {},
        **changes,
    }


class StubLLM:
    def __init__(self, value):
        self.value = value
        self.calls = []

    async def call_structured(self, **kwargs):
        self.calls.append(json.loads(kwargs["user_prompt"]))
        return SimpleNamespace(data={"needs_manual": False, "mode": "value", "value": self.value})


async def test_automatic_positive_and_negative_resolution():
    llm = StubLLM("person@example.test")
    assert await resolve_input(binding(configured_value="person@example.test"), llm) == {"mode": "value", "value": "person@example.test"}
    assert not llm.calls
    assert await resolve_input(
        binding(category="NEGATIVE", negative_target=True, objective="Reject invalid email format"),
        llm,
    ) is None
    assert await resolve_input(
        binding(category="NEGATIVE", negative_target=True, allow_empty=True), llm
    ) == {"mode": "empty", "value": ""}
    assert await resolve_input(binding(requires_fixture=True), llm) is None
    assert await resolve_input(binding(field="Password", sensitive=True), llm) is None
    assert await resolve_input(binding(field="Password", sensitive=True, category="NEGATIVE", negative_target=True, objective="Reject incorrect password"), llm) is None


async def test_llm_cannot_make_negative_target_valid_or_invent_unknown_negative_rule():
    assert (
        await resolve_input(
            binding(
                category="NEGATIVE", negative_target=True, objective="Reject a disallowed email domain"
            ),
            StubLLM("person@example.test"),
        )
        is None
    )
    assert (
        await resolve_input(
            binding(field="Reference", category="NEGATIVE", negative_target=True),
            StubLLM("made-up-record"),
        )
        is None
    )
    assert await resolve_input(binding(), StubLLM("not-an-email")) is None


def test_snapshot_preserves_manual_values_empty_and_isolated_runs(tmp_path):
    item = binding(allow_empty=True)
    write_json(tmp_path / "manifest.json", {"input_bindings": [item]})
    write_json(
        tmp_path / "execution-inputs.json",
        {"values": {item["key"]: {"mode": "value", "value": "  abc@  "}}},
    )
    first = prepare_run_inputs(tmp_path, uuid4())
    write_json(
        tmp_path / "execution-inputs.json",
        {"values": {item["key"]: {"mode": "empty", "value": ""}}},
    )
    second = prepare_run_inputs(tmp_path, uuid4())
    assert read_json(first / "snapshot.json")["values"][item["key"]]["value"] == "  abc@  "
    assert read_json(second / "snapshot.json")["values"][item["key"]]["mode"] == "empty"
    with pytest.raises(ValueError):
        validate_answer({"mode": "empty", "value": ""}, binding())
    with pytest.raises(ValueError):
        validate_answer({"mode": "value", "value": ""}, binding())


def test_stale_binding_rejected_and_run_answers_excluded_from_download(tmp_path):
    write_json(tmp_path / "manifest.json", {"input_bindings": [binding()]})
    write_json(
        tmp_path / "execution-inputs.json",
        {"values": {"old-step": {"mode": "value", "value": "secret"}}},
    )
    with pytest.raises(ValueError, match="stale"):
        prepare_run_inputs(tmp_path, uuid4())
    blob = build_zip(tmp_path, tmp_path)
    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(blob)) as archive:
        assert "execution-inputs.json" not in archive.namelist()
        assert not any(".execution-inputs" in name for name in archive.namelist())


async def test_manual_answer_ownership_expiry_and_duplicate_submission(tmp_path, monkeypatch):
    run = SimpleNamespace(status="RUNNING")

    async def directory(*args):
        return run, tmp_path

    monkeypatch.setattr(executions, "_input_directory", directory)
    item = binding()
    identifier = uuid4()
    write_json(tmp_path / "bindings.json", {item["key"]: item})
    write_json(
        tmp_path / f"{identifier}.request.json",
        {
            "id": str(identifier),
            "key": item["key"],
            "status": "WAITING_FOR_INPUT",
            "expires_at": time.time() + 60,
        },
    )
    args = (uuid4(), uuid4(), identifier, executions.ExecutionInputAnswer(value="abc@"), None)
    assert await executions.answer_execution_input(*args) == {"status": "RESOLVED"}
    with pytest.raises(HTTPException) as duplicate:
        await executions.answer_execution_input(*args)
    assert duplicate.value.status_code == 409
    run.status = "COMPLETED"
    with pytest.raises(HTTPException) as finished:
        await executions.answer_execution_input(*args)
    assert finished.value.status_code == 409


async def test_input_draft_revision_validation_and_sensitive_masking(tmp_path, monkeypatch):
    item = binding(field="Password", sensitive=True)
    manifest = {"input_runtime_version": 1, "input_bindings": [item]}

    async def suite(*args):
        return tmp_path, manifest

    class Session:
        async def execute(self, *args):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(executions, "_input_suite", suite)
    session = Session()
    project_id, generation_id = uuid4(), uuid4()
    body = executions.ExecutionInputOverrides(expected_revision=0, values={item["key"]: {"mode": "value", "value": "manual-secret"}})
    assert await executions.save_execution_input_draft(project_id, generation_id, body, session) == {"revision": 1}
    public = await executions.get_execution_input_draft(project_id, generation_id, session)
    assert public["values"][item["key"]]["value"] == ""
    assert read_json(tmp_path / "execution-inputs.json")["values"][item["key"]]["value"] == "manual-secret"
    with pytest.raises(HTTPException) as stale:
        await executions.save_execution_input_draft(project_id, generation_id, body, session)
    assert stale.value.status_code == 409
    remove = executions.ExecutionInputOverrides(expected_revision=1, values={item["key"]: None})
    await executions.save_execution_input_draft(project_id, generation_id, remove, session)
    assert read_json(tmp_path / "execution-inputs.json")["values"] == {}


async def test_execution_request_is_project_scoped_and_expired_answer_rejected(tmp_path, monkeypatch):
    class Repository:
        def __init__(self, session):
            pass

        async def get_run_for_project(self, project_id, run_id):
            return None

    monkeypatch.setattr(executions, "ExecutionRepository", Repository)
    with pytest.raises(HTTPException) as missing:
        await executions._input_directory(uuid4(), uuid4(), None)
    assert missing.value.status_code == 404

    async def directory(*args):
        return SimpleNamespace(status="RUNNING"), tmp_path

    monkeypatch.setattr(executions, "_input_directory", directory)
    identifier = uuid4()
    write_json(tmp_path / f"{identifier}.request.json", {"status": "WAITING_FOR_INPUT", "expires_at": time.time() - 1})
    with pytest.raises(HTTPException) as expired:
        await executions.answer_execution_input(uuid4(), uuid4(), identifier, executions.ExecutionInputAnswer(value="input"), None)
    assert expired.value.status_code == 409


@pytest.mark.parametrize("manual", [False, True])
async def test_generated_node_checkpoint_automatically_resumes_or_accepts_manual(
    tmp_path, monkeypatch, manual
):
    """Execute the generated TS checkpoint client against the real coordinator.

    Browser actions/assertions are not mocked here because none are involved in
    input exchange; only Playwright's test-info timeout accessor is stubbed.
    """
    node = shutil.which("node")
    typescript = Path(__file__).resolve().parents[4] / "QA-testing-Frontend/node_modules/typescript"
    if not node or not typescript.exists():
        pytest.skip("Node and the frontend TypeScript package are required")
    item = binding(sensitive=manual, field="Business token" if manual else "Email",
                   configured_value=None if manual else "person@example.test")
    write_json(tmp_path / "bindings.json", {item["key"]: item})
    write_json(tmp_path / "snapshot.json", {"values": {
        item["key"]: {"mode": "value", "value": "fixture-token", "source": "manual"}
    } if manual else {}})
    (tmp_path / "runtime.ts").write_text(RUNTIME_SOURCE, encoding="utf-8")
    driver = r"""
const fs = require("fs");
const ts = require(process.argv[2]);
const Module = require("module");
const original = Module._load;
Module._load = function(name, ...rest) {
  if (name === "@playwright/test") return { test: { info: () => ({ timeout: 1000, setTimeout: () => {} }), skip: () => { throw Error("Skipped"); } } };
  return original.call(this, name, ...rest);
};
const source = ts.transpileModule(fs.readFileSync("runtime.ts", "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
fs.writeFileSync("runtime.cjs", source);
require("./runtime.cjs").executionInput("TC-001:v1:step2", null).then(value => console.log("ANSWER=" + value)).catch(error => { console.error(error); process.exitCode = 1; });
"""
    (tmp_path / "driver.cjs").write_text(driver, encoding="utf-8")
    coordinator = asyncio.create_task(coordinate_inputs(tmp_path))
    process = await asyncio.create_subprocess_exec(
        node,
        "driver.cjs",
        str(typescript),
        cwd=tmp_path,
        env={**os.environ, "EXECUTION_INPUT_DIR": str(tmp_path)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        assert process.returncode == 0, stderr.decode()
        assert (
            "ANSWER=fixture-token" if manual else "ANSWER=person@example.test"
        ) in stdout.decode()
        used = actual_inputs(tmp_path, "TC-001")
        assert used[0]["value"] == ("••••••" if manual else "person@example.test")
        assert used[0]["source"] == ("manual" if manual else "automatic")
    finally:
        coordinator.cancel()
        with suppress(asyncio.CancelledError):
            await coordinator
        if process.returncode is None:
            process.kill()
            await process.wait()
