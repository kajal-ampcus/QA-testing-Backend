"""Store encrypted answers for forms discovered at crawl time."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_form_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("page_key", sa.String(1000), nullable=False),
        sa.Column("form_key", sa.String(64), nullable=False),
        sa.Column("encrypted_values", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "project_id",
            "page_key",
            "form_key",
            name="uq_discovery_form_answers_project_page_form",
        ),
    )
    op.create_index(
        "ix_discovery_form_answers_project_id",
        "discovery_form_answers",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_discovery_form_answers_project_id", table_name="discovery_form_answers")
    op.drop_table("discovery_form_answers")
