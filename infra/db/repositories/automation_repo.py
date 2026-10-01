"""
Automation script repository. Append-only versions, same transaction rules as
test cases: the caller commits.
"""

import uuid
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import selectinload

from infra.db.models.automation import AutomationScript, AutomationVersion
from infra.db.models.project import Project
from infra.db.repositories.base_repo import BaseRepository


class AutomationRepository(BaseRepository):
    async def next_script_code(self, project_id: uuid.UUID) -> str:
        await self.session.execute(
            select(Project.id).where(Project.id == project_id).with_for_update()
        )
        latest = await self.session.scalar(
            select(
                func.max(
                    cast(func.substring(AutomationScript.script_code, r"(\d+)$"), Integer)
                )
            ).where(AutomationScript.project_id == project_id)
        )
        return f"AUTO-{(latest or 0) + 1:03d}"

    def add_script(self, script: AutomationScript, version_data: dict[str, Any]) -> AutomationVersion:
        self.session.add(script)
        version = AutomationVersion(
            automation_script_id=script.id,
            version=script.current_version,
            generation_id=script.generation_id,
            file_path=version_data["file_path"],
            selector_strategy=version_data["selector_strategy"],
            risk_level=version_data["risk_level"],
            review_status=version_data["review_status"],
            review_findings=version_data["review_findings"],
            suite_summary=version_data["suite_summary"],
        )
        self.session.add(version)
        return version

    def append_version(
        self, script: AutomationScript, review_status: str
    ) -> AutomationVersion:
        script.current_version += 1
        script.review_status = review_status
        version = AutomationVersion(
            automation_script_id=script.id,
            version=script.current_version,
            generation_id=script.generation_id,
            file_path=script.file_path,
            selector_strategy=script.selector_strategy,
            risk_level=script.risk_level,
            review_status=review_status,
            review_findings=script.review_findings,
            suite_summary=script.suite_summary,
        )
        self.session.add(version)
        return version

    async def list_for_project(self, project_id: uuid.UUID) -> list[AutomationScript]:
        result = await self.session.execute(
            select(AutomationScript)
            .where(AutomationScript.project_id == project_id)
            .order_by(AutomationScript.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_generation(
        self, project_id: uuid.UUID, generation_id: uuid.UUID
    ) -> list[AutomationScript]:
        result = await self.session.execute(
            select(AutomationScript)
            .where(
                AutomationScript.project_id == project_id,
                AutomationScript.generation_id == generation_id,
            )
            .options(selectinload(AutomationScript.versions))
            .order_by(AutomationScript.script_code)
        )
        return list(result.scalars().all())
