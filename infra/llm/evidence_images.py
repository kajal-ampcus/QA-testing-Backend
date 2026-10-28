"""Load only UUID discovery screenshots from the configured evidence directory."""

import base64
import os
import uuid
from pathlib import Path
from typing import Any


def discovery_images(states: list[dict[str, Any]]) -> list[tuple[str, str]]:
    directory = Path(os.environ.get("DISCOVERY_EVIDENCE_DIR", "artifacts/discovery")).resolve()
    images = []
    for state in states:
        ref = str(state.get("evidence_ref") or "")
        filename = ref.rsplit("/", 1)[-1]
        try:
            identifier = uuid.UUID(Path(filename).stem)
        except ValueError:
            continue
        if filename != f"{identifier}.png":
            continue
        target = (directory / filename).resolve()
        if target.parent != directory or not target.is_file():
            continue
        try:
            if target.stat().st_size > 8 * 1024 * 1024:
                continue
            content = target.read_bytes()
        except OSError:
            continue
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            images.append((state["state_code"], "data:image/png;base64," + base64.b64encode(content).decode("ascii")))
    return images
