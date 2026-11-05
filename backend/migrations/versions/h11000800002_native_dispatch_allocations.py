"""Explicit immutable outbound sources for native arrivals; no invented backfill."""
from alembic import op
import sqlalchemy as sa
revision='h11000800002'
down_revision='h11000800001'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('arrival_dispatch_allocations',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('arrival_id',sa.String(36),sa.ForeignKey('cargo_arrivals.id'),nullable=False),
        sa.Column('stock_event_id',sa.String(36),sa.ForeignKey('stock_events.id'),nullable=False),
        sa.Column('source_version',sa.Integer(),nullable=False),sa.Column('receipt_id',sa.String(36),sa.ForeignKey('receipts.id'),nullable=False),
        sa.Column('pieces',sa.Integer(),nullable=False),sa.Column('kg',sa.Numeric(20,6),nullable=False),sa.Column('cbm',sa.Numeric(20,6),nullable=False),
        sa.UniqueConstraint('arrival_id','stock_event_id'),sa.CheckConstraint('pieces > 0 AND kg >= 0 AND cbm >= 0'))
    op.create_index('ix_arrival_dispatch_allocations_arrival_id','arrival_dispatch_allocations',['arrival_id'])
    op.create_index('ix_arrival_dispatch_allocations_stock_event_id','arrival_dispatch_allocations',['stock_event_id'])


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM arrival_dispatch_allocations LIMIT 1')).first():raise RuntimeError('Cannot discard nonempty actual native outbound allocations')
    op.drop_table('arrival_dispatch_allocations')
