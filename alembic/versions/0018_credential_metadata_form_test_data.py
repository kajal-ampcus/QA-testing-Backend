"""Add credential scenario metadata and project form test fixtures."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discovery_credentials", sa.Column("test_type", sa.String(50), nullable=False, server_default="positive"))
    op.add_column("discovery_credentials", sa.Column("credential_type", sa.String(50), nullable=False, server_default="login"))
    op.add_column("discovery_credentials", sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"))
    op.create_table(
        "form_test_data",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("test_cases.id"), nullable=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("form_group", sa.String(120), nullable=False),
        sa.Column("test_type", sa.String(50), nullable=False),
        sa.Column("test_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_form_test_data_project_id", "form_test_data", ["project_id"])
    op.create_index("ix_form_test_data_test_case_id", "form_test_data", ["test_case_id"])
    op.create_index("ix_form_test_data_form_group", "form_test_data", ["form_group"])
    op.create_index(
        "uq_form_test_data_case_active",
        "form_test_data",
        ["project_id", "test_case_id"],
        unique=True,
        postgresql_where=sa.text("active AND test_case_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_form_test_data_case_active", table_name="form_test_data")
    op.drop_index("ix_form_test_data_form_group", table_name="form_test_data")
    op.drop_index("ix_form_test_data_test_case_id", table_name="form_test_data")
    op.drop_index("ix_form_test_data_project_id", table_name="form_test_data")
    op.drop_table("form_test_data")
    op.drop_column("discovery_credentials", "tags")
    op.drop_column("discovery_credentials", "credential_type")
    op.drop_column("discovery_credentials", "test_type")
