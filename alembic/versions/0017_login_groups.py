"""Group reusable accounts by observed login route and role."""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("discovery_credentials", sa.Column("login_group", sa.String(200), nullable=True))
    op.add_column("discovery_credentials", sa.Column("login_route", sa.String(2000), nullable=True))


def downgrade():
    op.drop_column("discovery_credentials", "login_route")
    op.drop_column("discovery_credentials", "login_group")
