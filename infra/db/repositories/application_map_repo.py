"""
ApplicationMap repository. Two N+1-avoidance patterns live here:

1. get_with_states() / get_latest_for_project() always use
   selectinload(ApplicationMap.states) — fetching a map with 150 states is
   two queries total (one for the map, one `WHERE application_map_id IN
   (...)` for all its states), never 151 lazy-loaded ones.
2. fingerprint_exists() is a single indexed EXISTS query, not "fetch every
   state and check in Python" — this is called on every discovered element,
   potentially hundreds of times per crawl, so it has to stay O(1) against
   the unique index on the table, not grow with map size.
"""

import uuid
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import selectinload

from infra.db.models.application_map import ApplicationMap, ApplicationMapState
from infra.db.models.project import Project
from infra.db.repositories.base_repo import BaseRepository


class ApplicationMapRepository(BaseRepository):
    async def create(self, project_id: uuid.UUID, base_url: str) -> ApplicationMap:
        project = await self.session.execute(
            select(Project.id).where(Project.id == project_id).with_for_update()
        )
        if project.scalar_one_or_none() is None:
            raise ValueError("Project not found")
        result = await self.session.execute(
            select(func.coalesce(func.max(ApplicationMap.version), 0)).where(
                ApplicationMap.project_id == project_id
            )
        )
        next_version = result.scalar_one() + 1
        app_map = ApplicationMap(
            project_id=project_id, base_url=base_url, version=next_version, status="RUNNING"
        )
        self.session.add(app_map)
        await self.session.flush()
        return app_map

    async def add_state(
        self,
        application_map_id: uuid.UUID,
        state_code: str,
        url_pattern: str,
        fingerprint: str,
        reached_via: list[str],
        elements: list[dict[str, Any]],
        evidence_ref: str | None = None,
    ) -> ApplicationMapState:
        state = ApplicationMapState(
            application_map_id=application_map_id,
            state_code=state_code,
            url_pattern=url_pattern,
            fingerprint=fingerprint,
            reached_via=reached_via,
            elements=elements,
            evidence_ref=evidence_ref,
        )
        self.session.add(state)
        await self.session.flush()
        return state

    async def fingerprint_exists(self, application_map_id: uuid.UUID, fingerprint: str) -> bool:
        result = await self.session.execute(
            select(
                exists().where(
                    ApplicationMapState.application_map_id == application_map_id,
                    ApplicationMapState.fingerprint == fingerprint,
                )
            )
        )
        return bool(result.scalar())

    async def set_status(
        self, application_map_id: uuid.UUID, status: str, termination_reason: str | None = None,
        coverage: dict[str, Any] | None = None,
    ) -> None:
        app_map = await self.session.get(ApplicationMap, application_map_id)
        if app_map is not None:
            app_map.status = status
            app_map.termination_reason = termination_reason
            if coverage is not None:
                app_map.coverage = coverage
            await self.session.flush()

    async def get_with_states(self, application_map_id: uuid.UUID) -> ApplicationMap | None:
        result = await self.session.execute(
            select(ApplicationMap)
            .where(ApplicationMap.id == application_map_id)
            .options(selectinload(ApplicationMap.states))
        )
        return result.scalar_one_or_none()

    async def get_latest_for_project(self, project_id: uuid.UUID) -> ApplicationMap | None:
        result = await self.session.execute(
            select(ApplicationMap)
            .where(ApplicationMap.project_id == project_id)
            .order_by(ApplicationMap.version.desc(), ApplicationMap.created_at.desc())
            .limit(1)
            .options(selectinload(ApplicationMap.states))
        )
        return result.scalar_one_or_none()

    async def count_states(self, application_map_id: uuid.UUID) -> int:
        """Used for crawl-budget checks (max_pages) without loading every
        state's full elements[] JSONB payload into memory just to count rows."""
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.count())
            .select_from(ApplicationMapState)
            .where(ApplicationMapState.application_map_id == application_map_id)
        )
        return result.scalar_one()
