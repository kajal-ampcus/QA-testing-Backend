"""Add optional expiry to encrypted discovery credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("discovery_credentials")}
    if "expires_at" not in columns:
        op.add_column("discovery_credentials", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("discovery_credentials", "expires_at")
