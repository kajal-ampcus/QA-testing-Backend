"""Pin approvals to a target version; keep requirement external references."""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("target_version", sa.Integer(), nullable=True))
    op.add_column("requirements", sa.Column("external_ref", sa.String(200), nullable=True))
    # Existing pending requirement approvals were requested for the version
    # that is current now.
    op.execute(
        """
        UPDATE approvals a
        SET target_version = r.current_version
        FROM requirements r
        WHERE a.target_type = 'requirement' AND a.target_id = r.id AND a.status = 'PENDING'
        """
    )


def downgrade() -> None:
    op.drop_column("requirements", "external_ref")
    op.drop_column("approvals", "target_version")
