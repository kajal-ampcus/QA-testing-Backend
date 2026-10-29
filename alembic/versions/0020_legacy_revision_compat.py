"""Restore the legacy revision marker present in deployed databases.

Some environments were stamped at revision 0020 before that migration file was
added to this source tree.  This compatibility revision deliberately makes no
schema changes; it restores a complete Alembic revision graph so later
migrations can run safely.
"""

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
