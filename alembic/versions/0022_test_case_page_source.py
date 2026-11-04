"""Store a test case's primary observed state and origin."""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("test_cases")}
    if "primary_state_code" not in columns:
        op.add_column("test_cases", sa.Column("primary_state_code", sa.String(50), nullable=True))
    if "source" not in columns:
        op.add_column("test_cases", sa.Column("source", sa.String(30), nullable=True))
    if "import_metadata" not in columns:
        op.add_column("test_cases", sa.Column("import_metadata", sa.JSON(), nullable=True))

def downgrade() -> None:
    op.drop_column("test_cases", "import_metadata")
    op.drop_column("test_cases", "source")
    op.drop_column("test_cases", "primary_state_code")
