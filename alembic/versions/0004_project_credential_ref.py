"""Store an opaque discovery credential reference on projects."""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("credential_ref", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "credential_ref")
