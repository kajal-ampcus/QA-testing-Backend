"""
Liveness/readiness endpoints. Safe to implement fully in Phase 0 — no domain
dependency for /health; /health/db is the one deliberate small real piece
added to verify Postgres connectivity (see infra/db/session.py's note).
"""

from fastapi import APIRouter
from sqlalchemy import text

from infra.db.session import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
async def health_db() -> dict[str, str]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}
