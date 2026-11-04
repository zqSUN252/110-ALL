"""Preserve unknown nonessential SEA KG through actual observed batches."""
from alembic import op
import sqlalchemy as sa
revision='f11000500003'
down_revision='f11000500002'
branch_labels=None
depends_on=None

def upgrade():
    op.alter_column('cargo_arrivals','kg',existing_type=sa.Numeric(20,6),nullable=True)
    op.alter_column('physical_releases','kg',existing_type=sa.Numeric(20,6),nullable=True)

def downgrade():
    for table in ('cargo_arrivals','physical_releases'):
        if op.get_bind().execute(sa.text(f'SELECT EXISTS(SELECT 1 FROM {table} WHERE kg IS NULL)')).scalar():raise RuntimeError('Refuse loss of unknown observed KG')
        op.alter_column(table,'kg',existing_type=sa.Numeric(20,6),nullable=False)
