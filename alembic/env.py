"""
Alembic migration environment.

Imports every SQLAlchemy model from infra/db/models/ so autogenerate can see
the full schema. Milestone 1 only has 5 models (project, requirement +
requirement_versions, approval, agent_run, audit_log) — later milestones add
their models' imports here as infra/db/models/ gains real content.
"""

import asyncio
from logging.config import fileConfig

import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# Import every model module so its table registers on Base.metadata.
# Required even though these names aren't referenced directly below —
# the import side effect is what populates Base.metadata.
from infra.db.models import (  # noqa: F401
    Base,
    agent_run,
    application_map,
    approval,
    audit_log,
    automation,
    discovery_credential,
    execution,
    project,
    requirement,
    test_case,
)
from infra.db.session import DATABASE_URL

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: sqlalchemy.engine.Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(DATABASE_URL)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
