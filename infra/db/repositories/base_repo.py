"""
Shared base repository. Deliberately thin (docs/PROJECT_STRUCTURE.md point 8)
— just holds the session; each concrete repository still owns its own query
surface rather than inheriting a generic CRUD-everything base.
"""

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
