"""Which project currently owns the shared execution browser.

One worker display is streamed to every Execution page. The page only shows
that stream when the key below names the project being viewed.
"""

from __future__ import annotations

from uuid import UUID

from infra.queue.broker import get_arq_pool

LIVE_OWNER_KEY = "execution:live-owner"


async def claim_live_display(project_id: str, run_id: str) -> None:
    try:
        pool = await get_arq_pool()
        await pool.set(LIVE_OWNER_KEY, f"{project_id}:{run_id}", ex=7200)
    except Exception:  # noqa: BLE001 - a missing display key must not fail the run
        return


async def release_live_display(run_id: str) -> None:
    try:
        pool = await get_arq_pool()
        current = await pool.get(LIVE_OWNER_KEY)
        if current is None:
            return
        text = current.decode() if isinstance(current, bytes) else str(current)
        if text.endswith(f":{run_id}"):
            await pool.delete(LIVE_OWNER_KEY)
    except Exception:  # noqa: BLE001 - clearing the display key is best-effort
        return


async def current_live_display() -> tuple[UUID, UUID] | None:
    try:
        pool = await get_arq_pool()
        current = await pool.get(LIVE_OWNER_KEY)
    except Exception:  # noqa: BLE001 - the page hides the browser when the key cannot be read
        return None
    if not current:
        return None
    text = current.decode() if isinstance(current, bytes) else str(current)
    project_id, _, run_id = text.partition(":")
    try:
        return UUID(project_id), UUID(run_id)
    except ValueError:
        return None
