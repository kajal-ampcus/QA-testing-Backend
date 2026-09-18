"""
Shared FastAPI dependency-injection providers.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from infra.db.session import AsyncSessionLocal


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


# TODO (later milestone): get_current_user, get_tool_gateway once auth
# and the Tool Gateway itself have real content.
