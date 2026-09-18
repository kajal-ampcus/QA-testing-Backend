"""Record discovery termination reason and coverage."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_maps", sa.Column("termination_reason", sa.String(length=100), nullable=True))
    op.add_column(
        "application_maps",
        sa.Column("coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("application_maps", "coverage")
    op.drop_column("application_maps", "termination_reason")
