"""Preserve unknown source receipt/quantities without inventing zero or identities."""
from alembic import op
import sqlalchemy as sa

revision='c11000300004'
down_revision='c11000300003'
branch_labels=None
depends_on=None


def upgrade():
    op.alter_column('tickets','receipt',existing_type=sa.String(200),nullable=True)
    op.alter_column('tickets','pieces',existing_type=sa.Integer(),nullable=True)
    op.alter_column('tickets','kg',existing_type=sa.Numeric(20,6),nullable=True)
    op.alter_column('tickets','cbm',existing_type=sa.Numeric(20,6),nullable=True)


def downgrade():
    if op.get_bind().execute(sa.text('SELECT EXISTS(SELECT 1 FROM tickets WHERE receipt IS NULL OR pieces IS NULL OR kg IS NULL OR cbm IS NULL)')).scalar():
        raise RuntimeError('Unknown source values must remain unknown; cannot downgrade pending tickets')
    op.alter_column('tickets','receipt',existing_type=sa.String(200),nullable=False)
    op.alter_column('tickets','pieces',existing_type=sa.Integer(),nullable=False)
    op.alter_column('tickets','kg',existing_type=sa.Numeric(20,6),nullable=False)
    op.alter_column('tickets','cbm',existing_type=sa.Numeric(20,6),nullable=False)
