"""Filesystem helpers confined to the automation artifact directory."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from uuid import UUID

_SKIP_DIRS = {"node_modules", "test-results", "playwright-report", "blob-report"}
_SKIP_FILES = {".env"}


class ArtifactPathError(ValueError):
    pass


def ensure_under(root: Path, candidate: Path) -> Path:
    base = root.resolve()
    target = candidate.resolve()
    if target != base and base not in target.parents:
        raise ArtifactPathError("Path escapes the automation artifact directory")
    return target


def generation_dir(root: Path, project_id: UUID, generation_id: UUID) -> Path:
    return ensure_under(root, root / str(project_id) / str(generation_id))


def build_zip(root: Path, suite_dir: Path) -> bytes:
    suite = ensure_under(root, suite_dir)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(suite.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(suite)
            if any(part in _SKIP_DIRS for part in relative.parts):
                continue
            if path.name in _SKIP_FILES:
                continue
            archive.write(path, relative.as_posix())
    return buffer.getvalue()


def _host_folder(raw: str) -> str | None:
    """Absolute folder on the user's machine, including a Windows path seen from Linux."""
    text = raw.strip().replace("\\", "/")
    if not text:
        return None
    drive = ""
    rest = text
    if len(text) >= 2 and text[0].isalpha() and text[1] == ":":
        drive = text[:2].upper()
        rest = text[2:]
        if rest and not rest.startswith("/"):
            return None
    elif text.startswith("/"):
        rest = text
    else:
        return None
    parts: list[str] = []
    for part in rest.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    suffix = "/".join(parts)
    if drive:
        return f"{drive}/{suffix}" if suffix else f"{drive}/"
    return f"/{suffix}" if suffix else "/"


def ide_links(host_root: str, project_id: UUID, generation_id: UUID) -> dict[str, str] | None:
    """Return IDE URIs that open the generated suite folder on the host."""
    root = _host_folder(host_root)
    if root is None:
        return None
    folder = f"{root.rstrip('/')}/{project_id}/{generation_id}"
    if not folder.startswith(root.rstrip("/") + "/"):
        return None
    return {
        "vscode": f"vscode://file/{folder}",
        "cursor": f"cursor://file/{folder}",
    }


def list_files(suite_dir: Path) -> list[str]:
    files: list[str] = []
    for path in sorted(suite_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(suite_dir)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.name in _SKIP_FILES:
            continue
        files.append(relative.as_posix())
    return files


def read_sources(suite_dir: Path, limit: int = 200_000) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for relative in list_files(suite_dir):
        path = suite_dir / relative
        if path.suffix.lower() not in {".ts", ".json", ".md", ".example", ".gitignore"} and path.name not in {
            ".gitignore",
            ".env.example",
            "package.json",
        }:
            continue
        text = path.read_text(encoding="utf-8")
        if len(text) > limit:
            text = text[:limit] + "\n/* truncated */\n"
        sources.append({"path": relative, "content": text})
    return sources
