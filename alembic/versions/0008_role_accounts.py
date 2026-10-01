"""Named role accounts and immutable access context for maps and tests."""
import sqlalchemy as sa

from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('discovery_credentials', sa.Column('label',sa.String(120),nullable=False,server_default='Test account'))
    op.add_column('discovery_credentials', sa.Column('role',sa.String(120),nullable=False,server_default='User'))
    op.add_column('discovery_credentials', sa.Column('active',sa.Boolean(),nullable=False,server_default=sa.true()))
    for table in ('application_maps','test_cases'):
        op.add_column(table,sa.Column('credential_ref',sa.String(200),nullable=True))
        op.add_column(table,sa.Column('account_label',sa.String(120),nullable=True))
        op.add_column(table,sa.Column('account_role',sa.String(120),nullable=True))

def downgrade():
    for table in ('test_cases','application_maps'):
        for column in ('account_role','account_label','credential_ref'):
            op.drop_column(table,column)
    for column in ('active','role','label'):
        op.drop_column('discovery_credentials',column)
