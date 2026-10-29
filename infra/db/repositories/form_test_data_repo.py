"""Eligibility boundary for explicitly contextualized discovery fixtures."""

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from infra.db.models.form_test_data import FormTestData
from infra.db.models.project import Project
from infra.db.models.test_case import TestCase
from infra.db.session import AsyncSessionLocal

if TYPE_CHECKING:
    from core.tool_gateway.form_resolution import FormContext


async def discovery_fixture(context: "FormContext") -> dict[str, Any]:
    # Library labels are not page identities. Never guess a case, account,
    # scenario or form group from similar labels during a general crawl.
    if not context.test_case_id or not context.scenario or not context.form_group or context.login:
        return {}
    async with AsyncSessionLocal() as session:
        case = await session.get(TestCase, context.test_case_id)
        if case is None or case.project_id != context.project_id:
            return {}
        project = await session.get(Project, context.project_id)
        if (
            project is None
            or (case.credential_ref or project.credential_ref) != context.credential_ref
        ):
            return {}
        rows = list(
            (
                await session.scalars(
                    select(FormTestData).where(
                        FormTestData.project_id == context.project_id,
                        FormTestData.test_case_id == case.id,
                        FormTestData.active.is_(True),
                        FormTestData.test_type == context.scenario,
                        FormTestData.form_group == context.form_group,
                    )
                )
            ).all()
        )
        return dict(rows[0].test_data) if len(rows) == 1 else {}
