"""
Add test_cases and test_case_versions tables (Agent 3 — Test Design).

Revision: 0007
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("tc_code", sa.String(50), nullable=False),
        sa.Column("requirement_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requirements.id"), nullable=False),
        sa.Column("requirement_version", sa.Integer, nullable=False),
        sa.Column("application_map_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("application_maps.id"), nullable=False),
        sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "tc_code", name="uq_test_cases_project_code"),
    )
    op.create_index("ix_test_cases_project_id", "test_cases", ["project_id"])
    op.create_index("ix_test_cases_requirement_id", "test_cases", ["requirement_id"])

    op.create_table(
        "test_case_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_cases.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("preconditions", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("steps", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("expected_result", sa.Text, nullable=False),
        sa.Column("test_data", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("traceability", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("category", sa.String(30), nullable=False, server_default="POSITIVE"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("test_case_id", "version", name="uq_test_case_versions_number"),
    )
    op.create_index("ix_test_case_versions_test_case_id", "test_case_versions", ["test_case_id"])


def downgrade() -> None:
    op.drop_table("test_case_versions")
    op.drop_table("test_cases")
