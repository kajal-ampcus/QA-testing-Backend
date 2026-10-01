"""
REQ -> TC traceability joins reconstructed via foreign keys.

Later milestones extend this with AUTO / RUN / FAIL / DEF once those tables
have real rows. Callers must not reimplement the join ad hoc.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from infra.db.models.requirement import Requirement
from infra.db.models.test_case import TestCase


async def trace_from_requirement(
    session: AsyncSession, requirement_id: uuid.UUID
) -> dict[str, Any] | None:
    requirement = await session.get(Requirement, requirement_id)
    if requirement is None:
        return None
    result = await session.execute(
        select(TestCase)
        .where(TestCase.requirement_id == requirement_id)
        .options(selectinload(TestCase.versions))
        .order_by(TestCase.created_at)
    )
    cases = []
    for test_case in result.scalars():
        version = next(
            (row for row in test_case.versions if row.version == test_case.current_version),
            None,
        )
        cases.append(
            {
                "id": str(test_case.id),
                "tc_code": test_case.tc_code,
                "status": test_case.status,
                "requirement_version": test_case.requirement_version,
                "traceability": list(version.traceability) if version else [],
            }
        )
    return {
        "requirement_id": str(requirement.id),
        "req_code": requirement.req_code,
        "requirement_version": requirement.current_version,
        "status": requirement.status,
        "test_cases": cases,
    }
