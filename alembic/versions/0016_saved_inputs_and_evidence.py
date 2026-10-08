"""Scope saved inputs by account and keep screenshot review metadata."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discovery_form_answers",
        sa.Column("credential_ref", sa.String(200), nullable=True),
    )
    op.add_column(
        "discovery_form_answers",
        sa.Column("credential_scope", sa.String(200), nullable=False, server_default=""),
    )
    op.drop_constraint(
        "uq_discovery_form_answers_project_page_form",
        "discovery_form_answers",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_discovery_form_answers_scope",
        "discovery_form_answers",
        ["project_id", "credential_scope", "page_key", "form_key"],
    )
    op.add_column(
        "application_map_states",
        sa.Column("evidence_sha256", sa.String(64), nullable=True),
    )
    op.create_table(
        "discovery_evidence_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="reviewed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "project_id",
            "fingerprint",
            name="uq_discovery_evidence_reviews_project_fingerprint",
        ),
    )


def downgrade() -> None:
    op.drop_table("discovery_evidence_reviews")
    op.drop_column("application_map_states", "evidence_sha256")
    op.drop_constraint("uq_discovery_form_answers_scope", "discovery_form_answers", type_="unique")
    op.create_unique_constraint(
        "uq_discovery_form_answers_project_page_form",
        "discovery_form_answers",
        ["project_id", "page_key", "form_key"],
    )
    op.drop_column("discovery_form_answers", "credential_scope")
    op.drop_column("discovery_form_answers", "credential_ref")
