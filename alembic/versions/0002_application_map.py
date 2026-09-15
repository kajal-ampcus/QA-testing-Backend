"""application map — Milestone 2 (application_maps, application_map_states)

Revision ID: 0002
Revises: 0001
Create Date: Milestone 2
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_maps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="RUNNING"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "application_map_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "application_map_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("application_maps.id"),
            nullable=False,
        ),
        sa.Column("state_code", sa.String(50), nullable=False),
        sa.Column("url_pattern", sa.String(1000), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("reached_via", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("elements", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("evidence_ref", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_application_map_states_map_fingerprint",
        "application_map_states",
        ["application_map_id", "fingerprint"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_application_map_states_map_fingerprint", table_name="application_map_states")
    op.drop_table("application_map_states")
    op.drop_table("application_maps")
