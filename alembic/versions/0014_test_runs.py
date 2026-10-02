"""Add test_runs and test_results."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.String(100), nullable=True),
        sa.Column("environment", sa.String(30), nullable=False, server_default="development"),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("run_destructive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="QUEUED"),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_test_runs_project_id", "test_runs", ["project_id"])
    op.create_index("ix_test_runs_generation_id", "test_runs", ["generation_id"])
    op.create_index("ix_test_runs_job_id", "test_runs", ["job_id"])

    op.create_table(
        "test_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "automation_script_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_scripts.id"),
            nullable=True,
        ),
        sa.Column("spec_path", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("assertion", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_test_results_run_id", "test_results", ["test_run_id"])
    op.create_index("ix_test_results_script_id", "test_results", ["automation_script_id"])


def downgrade() -> None:
    op.drop_table("test_results")
    op.drop_table("test_runs")
