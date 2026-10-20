"""Resolve configured input checkpoints without fabricating missing fixtures."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from infra.execution_inputs import read_json, write_json


async def resolve_input(binding: dict[str, Any], llm: Any = None) -> dict[str, str] | None:
    """Use configured fixtures or the explicitly requested empty condition."""
    # Recovery may use configured values or an explicit empty condition only.
    # Unknown credentials/business fixtures cannot be made valid by inventing data.
    configured = binding.get("configured_value")
    if isinstance(configured, str) and configured:
        return {"mode": "value", "value": configured}
    if binding.get("negative_target") and binding.get("allow_empty"):
        return {"mode": "empty", "value": ""}
    return None


async def coordinate_inputs(directory: Path) -> None:
    """Poll checkpoints concurrently with Playwright; bounded one-shot resolution."""
    attempted: set[str] = set()
    while True:
        for path in directory.glob("*.request.json"):
            request = read_json(path)
            identifier = request["id"]
            if identifier in attempted:
                continue
            attempted.add(identifier)
            binding = read_json(directory / "bindings.json").get(request["key"])
            if not binding:
                request.update(
                    status="BLOCKED",
                    reason="Unknown input binding; review the generated case",
                )
                write_json(path, request)
                continue
            if time.time() >= request["expires_at"]:
                continue
            request.update(status="RESOLVING_INPUT")
            write_json(path, request)
            try:
                answer = await resolve_input(binding)
            except Exception:
                # A coordinator failure is recorded as a test-visible error,
                # never converted into a prompt that blocks the whole suite.
                answer = None
            if (
                time.time() >= request["expires_at"]
                or (directory / f"{identifier}.consumed.json").exists()
            ):
                continue
            if answer is not None and not (directory / f"{identifier}.manual.json").exists():
                write_json(directory / f"{identifier}.auto.json", {**answer, "source": "automatic"})
                request.update(status="RESOLVED")
            else:
                # A user-supplied run draft can still override the automatic
                # value; it is not a required interaction.
                request.update(status="BLOCKED", reason="No configured test value is available. Configure the fixture and regenerate this case.")
            write_json(path, request)
        await asyncio.sleep(0.3)
