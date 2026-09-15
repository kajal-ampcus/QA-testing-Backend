"""
Requirement repository. Handles append-only requirement_versions writes
(architecture doc Section 23) and the REQ-ID + version lookups used
throughout the traceability chain. Does not commit internally — the caller
(a router, for Milestone 1) owns the transaction boundary so a requirement,
its agent_run row, and its approval row commit together atomically.
"""

import uuid
from typing import Any

from sqlalchemy import func, select

from domain.enums import RequirementStatus
from infra.db.models.project import Project
from infra.db.models.requirement import Requirement, RequirementVersion
from infra.db.repositories.base_repo import BaseRepository


class RequirementRepository(BaseRepository):
    async def next_req_code(self, project_id: uuid.UUID) -> str:
        """Simple sequential REQ-NNN per project. Not domain-tag-prefixed
        (e.g. REQ-EMP-001) yet — that refinement can come once domain_tags
        are reliably populated across enough real requirements to name from."""
        # Serialize code allocation within a project until the caller commits.
        project = await self.session.execute(
            select(Project.id).where(Project.id == project_id).with_for_update()
        )
        if project.scalar_one_or_none() is None:
            raise ValueError("Project not found")
        result = await self.session.execute(
            select(func.count()).select_from(Requirement).where(Requirement.project_id == project_id)
        )
        count = result.scalar_one()
        return f"REQ-{count + 1:03d}"

    async def create(
        self,
        project_id: uuid.UUID,
        req_code: str,
        raw_text: str,
        extraction: Any,  # validated agent output, kept out of infra's import graph
        status: RequirementStatus,
    ) -> tuple[Requirement, RequirementVersion]:
        requirement = Requirement(project_id=project_id, req_code=req_code, current_version=1, status=status)
        self.session.add(requirement)
        await self.session.flush()  # assigns requirement.id without committing

        version = RequirementVersion(
            requirement_id=requirement.id,
            version=1,
            raw_text=raw_text,
            title=extraction.title,
            description=extraction.description,
            acceptance_criteria=[ac.model_dump(mode="json") for ac in extraction.acceptance_criteria],
            ambiguities=[a.model_dump(mode="json") for a in extraction.ambiguities],
            domain_tags=extraction.domain_tags,
        )
        self.session.add(version)
        await self.session.flush()
        return requirement, version

    async def append_version(
        self, requirement: Requirement, raw_text: str, extraction: Any
    ) -> RequirementVersion:
        next_version = requirement.current_version + 1
        version = RequirementVersion(
            requirement_id=requirement.id,
            version=next_version,
            raw_text=raw_text,
            title=extraction.title,
            description=extraction.description,
            acceptance_criteria=[ac.model_dump(mode="json") for ac in extraction.acceptance_criteria],
            ambiguities=[a.model_dump(mode="json") for a in extraction.ambiguities],
            domain_tags=extraction.domain_tags,
        )
        self.session.add(version)
        requirement.current_version = next_version
        await self.session.flush()
        return version

    async def get_with_current_version(
        self, requirement_id: uuid.UUID
    ) -> tuple[Requirement, RequirementVersion] | None:
        requirement = await self.session.get(Requirement, requirement_id)
        if requirement is None:
            return None
        result = await self.session.execute(
            select(RequirementVersion).where(
                RequirementVersion.requirement_id == requirement_id,
                RequirementVersion.version == requirement.current_version,
            )
        )
        version = result.scalar_one()
        return requirement, version

    async def list_for_project(self, project_id: uuid.UUID) -> list[Requirement]:
        result = await self.session.execute(select(Requirement).where(Requirement.project_id == project_id))
        return list(result.scalars().all())

    async def set_status(self, requirement_id: uuid.UUID, status: RequirementStatus) -> None:
        requirement = await self.session.get(Requirement, requirement_id)
        if requirement is not None:
            requirement.status = status
            await self.session.flush()
