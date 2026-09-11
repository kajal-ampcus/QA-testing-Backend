"""Async SQLAlchemy engine and sessions shared by API database consumers."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infra.db.settings import DatabaseSettings

DATABASE_URL = DatabaseSettings().database_url
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield a session and close it when the consumer finishes."""
    async with AsyncSessionLocal() as session:
        yield session
