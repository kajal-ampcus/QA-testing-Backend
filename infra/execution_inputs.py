"""Run-scoped filesystem input exchange on the shared automation artifact volume.

The API and worker already share generated suites. Never put these files in a
downloaded suite: they may contain tester-entered secrets.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from core.agents.automation_generation.artifacts import ensure_under

SENSITIVE = re.compile(r"password|secret|token|credential|captcha|otp", re.I)


def run_input_dir(suite: Path, run_id: UUID) -> Path:
    return ensure_under(suite, suite / ".execution-inputs" / str(run_id))


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Input file must contain an object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def validate_answer(answer: dict[str, Any], binding: dict[str, Any]) -> dict[str, str]:
    mode = answer.get("mode", "value")
    value = answer.get("value")
    if mode not in {"value", "empty"} or not isinstance(value, str) or len(value) > 10000:
        raise ValueError("Supply a string value (up to 10000 characters) or explicit empty mode")
    if mode == "empty":
        if not binding.get("allow_empty"):
            raise ValueError("This step does not test empty input")
        value = ""
    elif value == "":
        raise ValueError("Use explicit empty mode for an intentional blank")
    # Do not strip whitespace: it can be the condition under test.
    return {"mode": mode, "value": value}


def prepare_run_inputs(suite: Path, run_id: UUID) -> Path:
    directory = run_input_dir(suite, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = read_json(suite / "manifest.json")
    bindings = {item["key"]: item for item in manifest.get("input_bindings", [])}
    draft = read_json(suite / "execution-inputs.json")
    if draft.get("schema_version", 1) != 1:
        raise ValueError("Unsupported execution input schema")
    values = draft.get("values", {})
    if not isinstance(values, dict):
        raise ValueError("execution-inputs.json values must be an object")
    resolved: dict[str, Any] = {}
    for key, answer in values.items():
        if key not in bindings:
            raise ValueError(f"Unknown or stale input binding: {key}")
        if not isinstance(answer, dict):
            raise ValueError(f"Invalid input entry: {key}")
        resolved[key] = {**validate_answer(answer, bindings[key]), "source": "manual"}
    write_json(directory / "snapshot.json", {"schema_version": 1, "values": resolved})
    write_json(directory / "bindings.json", bindings)
    return directory


def public_requests(directory: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted(directory.glob("*.request.json")):
        request = read_json(path)
        if (directory / f"{request['id']}.consumed.json").exists():
            continue
        # Answer values are deliberately excluded from the polling response.
        result.append({key: value for key, value in request.items() if key != "value"})
    return result


def actual_inputs(directory: Path, case_code: str) -> list[dict[str, str]]:
    fields = []
    for path in sorted(directory.glob("*.consumed.json")):
        item = read_json(path)
        if item.get("case_code") != case_code:
            continue
        name = str(item.get("field") or item.get("key") or "Input")
        value = (
            "••••••"
            if item.get("sensitive") or SENSITIVE.search(name)
            else str(item.get("value", ""))
        )
        fields.append({"name": name, "value": value, "source": str(item.get("source", ""))})
    return fields
