"""
Declarative Base for every ORM model in infra/db/models/. Imported by
alembic/env.py for autogenerate and by infra/db/session.py indirectly (via
each model file importing this Base).
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
