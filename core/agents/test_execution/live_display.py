"""Which project currently owns the shared execution browser.

One worker display is streamed to every Execution page. The page only shows
that stream when the key below names the project being viewed.
"""

from __future__ import annotations

from uuid import UUID

from infra.queue.broker import get_arq_pool

LIVE_OWNER_KEY = "execution:live-owner"
LIVE_LOG_PREFIX = "execution:live-log:"
_LOG_LIMIT = 6000


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


async def claim_live_display(project_id: str, run_id: str) -> None:
    try:
        pool = await get_arq_pool()
        await pool.set(LIVE_OWNER_KEY, f"{project_id}:{run_id}", ex=7200)
        await pool.delete(f"{LIVE_LOG_PREFIX}{run_id}")
    except Exception:  # noqa: BLE001 - a missing display key must not fail the run
        return


async def release_live_display(run_id: str) -> None:
    try:
        pool = await get_arq_pool()
        current = await pool.get(LIVE_OWNER_KEY)
        if current is not None and _text(current).endswith(f":{run_id}"):
            await pool.delete(LIVE_OWNER_KEY)
        await pool.delete(f"{LIVE_LOG_PREFIX}{run_id}")
    except Exception:  # noqa: BLE001 - clearing the display key is best-effort
        return


async def append_live_log(run_id: str, text: str) -> None:
    """Keep the tail of Playwright's output while the suite is still running."""
    if not text:
        return
    try:
        pool = await get_arq_pool()
        key = f"{LIVE_LOG_PREFIX}{run_id}"
        size = int(await pool.append(key, text))
        await pool.expire(key, 7200)
        if size > _LOG_LIMIT:
            tail = await pool.getrange(key, size - _LOG_LIMIT, -1)
            await pool.set(key, _text(tail), ex=7200)
    except Exception:  # noqa: BLE001 - a missing progress log must not fail the run
        return


async def current_live_log(run_id: str) -> str:
    try:
        pool = await get_arq_pool()
        return _text(await pool.get(f"{LIVE_LOG_PREFIX}{run_id}"))
    except Exception:  # noqa: BLE001 - the page still shows the browser without the log
        return ""


async def current_live_display() -> tuple[UUID, UUID] | None:
    try:
        pool = await get_arq_pool()
        current = await pool.get(LIVE_OWNER_KEY)
    except Exception:  # noqa: BLE001 - the page hides the browser when the key cannot be read
        return None
    if not current:
        return None
    text = _text(current)
    project_id, _, run_id = text.partition(":")
    try:
        return UUID(project_id), UUID(run_id)
    except ValueError:
        return None
