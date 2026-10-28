
"""Add opt-in live browser flag to test runs after existing migrations."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = [
        column["name"]
        for column in inspector.get_columns("test_runs")
    ]

    if "watch_browser" not in columns:
        op.add_column(
            "test_runs",
            sa.Column(
                "watch_browser",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    else:
        print("Column 'watch_browser' already exists. Skipping creation.")


def downgrade() -> None:
    op.drop_column("test_runs", "watch_browser")
