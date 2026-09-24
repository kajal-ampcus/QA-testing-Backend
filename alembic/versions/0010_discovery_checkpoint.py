"""Persist resumable application-discovery checkpoints."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application_maps",
        sa.Column("discovery_checkpoint", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("application_maps", "discovery_checkpoint")
