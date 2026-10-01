"""Add automation_scripts and automation_versions."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_scripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("script_code", sa.String(50), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_cases.id"), nullable=False),
        sa.Column("test_case_code", sa.String(50), nullable=False),
        sa.Column("test_case_version", sa.Integer(), nullable=False),
        sa.Column("requirement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requirement_version", sa.Integer(), nullable=True),
        sa.Column(
            "application_map_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("application_maps.id"),
            nullable=False,
        ),
        sa.Column("application_map_version", sa.Integer(), nullable=False),
        sa.Column("framework", sa.String(30), nullable=False, server_default="playwright"),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("selector_strategy", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("review_status", sa.String(30), nullable=False, server_default="REVIEWED"),
        sa.Column("review_findings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("suite_summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "script_code", name="uq_automation_scripts_project_code"),
    )
    op.create_index("ix_automation_scripts_project_id", "automation_scripts", ["project_id"])
    op.create_index("ix_automation_scripts_generation_id", "automation_scripts", ["generation_id"])

    op.create_table(
        "automation_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "automation_script_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_scripts.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("selector_strategy", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("review_findings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("suite_summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "automation_script_id", "version", name="uq_automation_versions_number"
        ),
    )
    op.create_index("ix_automation_versions_script_id", "automation_versions", ["automation_script_id"])
    op.create_index(
        "ix_automation_versions_generation_id", "automation_versions", ["generation_id"]
    )


def downgrade() -> None:
    op.drop_table("automation_versions")
    op.drop_table("automation_scripts")
