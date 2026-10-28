"""Add credential scenario metadata and project form test fixtures."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Revision 0018 was accidentally used by two independent migrations. Some
    # databases recorded the credential/data migration at 0018; others need
    # both schemas. Make this repair revision safe in either state.
    credential_columns = {column["name"] for column in inspector.get_columns("discovery_credentials")}
    for name, type_, default in (
        ("test_type", sa.String(50), "positive"),
        ("credential_type", sa.String(50), "login"),
        ("tags", postgresql.JSONB(astext_type=sa.Text()), "[]"),
    ):
        if name not in credential_columns:
            op.add_column(
                "discovery_credentials",
                sa.Column(name, type_, nullable=False, server_default=default),
            )

    test_run_columns = {column["name"] for column in inspector.get_columns("test_runs")}
    if "watch_browser" not in test_run_columns:
        op.add_column(
            "test_runs",
            sa.Column("watch_browser", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if "form_test_data" not in inspector.get_table_names():
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

    index_names = {index["name"] for index in sa.inspect(bind).get_indexes("form_test_data")}
    for name, columns in (
        ("ix_form_test_data_project_id", ["project_id"]),
        ("ix_form_test_data_test_case_id", ["test_case_id"]),
        ("ix_form_test_data_form_group", ["form_group"]),
    ):
        if name not in index_names:
            op.create_index(name, "form_test_data", columns)
    if "uq_form_test_data_case_active" not in index_names:
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
