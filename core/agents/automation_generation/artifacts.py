"""Filesystem helpers confined to the automation artifact directory."""

from __future__ import annotations

import io
import os
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


def ide_links(host_root: str, project_id: UUID, generation_id: UUID) -> dict[str, str] | None:
    """Return IDE URIs only for an absolute host root."""
    raw = host_root.strip()
    if not raw:
        return None
    root = Path(raw)
    if not root.is_absolute():
        return None
    root_abs = Path(os.path.abspath(root))
    target_abs = Path(os.path.abspath(root_abs / str(project_id) / str(generation_id)))
    if root_abs != target_abs and root_abs not in target_abs.parents:
        return None
    uri_path = target_abs.as_posix()
    return {
        "vscode": f"vscode://file/{uri_path}",
        "cursor": f"cursor://file/{uri_path}",
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
