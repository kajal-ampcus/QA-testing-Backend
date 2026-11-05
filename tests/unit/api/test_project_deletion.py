"""Exercise deletion against real foreign keys without touching the application DB."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import ARRAY, JSON, MetaData, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from apps.api.routers.v1 import projects as api
from infra.db.models import Base


class AsyncSessionAdapter:
    def __init__(self, session):
        self.session = session

    async def get(self, model, identity):
        return self.session.get(model, identity)

    async def execute(self, statement):
        return self.session.execute(statement)

    async def delete(self, row):
        self.session.delete(row)

    async def commit(self):
        self.session.commit()


@pytest.mark.asyncio
async def test_delete_project_removes_assigned_and_unassigned_fixtures(monkeypatch):
    engine = create_engine("sqlite://")
    metadata = MetaData()
    # Retain the real FK graph; omit unrelated required payloads in this fixture.
    for table in Base.metadata.tables.values():
        clone = table.to_metadata(metadata)
        for column in clone.columns:
            if isinstance(column.type, (JSONB, ARRAY)):
                column.type = JSON()
            if not column.primary_key:
                column.nullable = True
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        metadata.create_all(connection)
        projects = [uuid4(), uuid4()]
        for project_id in projects:
            connection.execute(metadata.tables["projects"].insert(), {"id": project_id, "name": "test"})
            requirement_id, map_id, case_id = uuid4(), uuid4(), uuid4()
            connection.execute(metadata.tables["requirements"].insert(), {"id": requirement_id, "project_id": project_id})
            connection.execute(metadata.tables["application_maps"].insert(), {"id": map_id, "project_id": project_id})
            connection.execute(metadata.tables["test_cases"].insert(), {"id": case_id, "project_id": project_id, "requirement_id": requirement_id, "application_map_id": map_id})
            for assigned in (True, False):
                connection.execute(metadata.tables["form_test_data"].insert(), {"id": uuid4(), "project_id": project_id, "test_case_id": case_id if assigned else None, "active": assigned})
    monkeypatch.setattr(api, "_ensure_no_active_discovery", AsyncMock())
    with Session(engine) as session:
        await api.delete_project(projects[0], AsyncSessionAdapter(session))
        assert session.get(api.Project, projects[0]) is None
        assert session.get(api.Project, projects[1]) is not None
        remaining = session.execute(select(api.FormTestData)).scalars().all()
        assert len(remaining) == 2
        assert all(row.project_id == projects[1] for row in remaining)
        assert len(session.execute(select(api.TestCase)).scalars().all()) == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_delete_project_retains_active_discovery_guard(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(api, "_ensure_no_active_discovery", AsyncMock(side_effect=HTTPException(409, "Stop discovery")))
    with pytest.raises(HTTPException) as error:
        await api.delete_project(uuid4(), session)
    assert error.value.status_code == 409
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()
