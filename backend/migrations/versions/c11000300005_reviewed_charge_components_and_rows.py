"""Immutable reviewed charge components and explicit source row disposition."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='c11000300005'
down_revision='c11000300004'
branch_labels=None
depends_on=None


def upgrade():
    op.alter_column('quote_snapshots','charge_ngn',existing_type=sa.Numeric(20,2),nullable=True)
    op.add_column('cargo_source_rows',sa.Column('disposition',sa.String(16),nullable=False,server_default='pending'))
    op.alter_column('cargo_source_rows','disposition',server_default=None)
    op.add_column('cargo_source_rows',sa.Column('exclusion_reason',sa.Text(),nullable=True))
    op.add_column('cargo_source_rows',sa.Column('reviewed_by',sa.String(36),sa.ForeignKey('users.id'),nullable=True))
    op.add_column('cargo_arrivals',sa.Column('charge_basis',postgresql.JSONB(),nullable=True))
    op.create_table('cargo_charge_components',
        sa.Column('quote_id',sa.String(36),sa.ForeignKey('quote_snapshots.id'),nullable=True),
        sa.Column('ticket_id',sa.String(36),sa.ForeignKey('tickets.id'),nullable=True),
        sa.Column('ticket_version',sa.Integer(),nullable=True),
        sa.Column('position',sa.Integer(),nullable=False),
        sa.Column('component',sa.String(12),nullable=False),
        sa.Column('currency',sa.String(6),nullable=False),
        sa.Column('unit',sa.String(6),nullable=False),
        sa.Column('amount',sa.Numeric(20,2),nullable=True),
        sa.Column('source',sa.Text(),nullable=False),
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint('(quote_id IS NULL) <> (ticket_id IS NULL)'),
        sa.CheckConstraint("component IN ('freight','clearing') AND currency IN ('USD','NGN') AND unit IN ('PCS','KG','CBM')"),
        sa.CheckConstraint('amount >= 0'),
        sa.UniqueConstraint('ticket_id','ticket_version','position'),
        sa.UniqueConstraint('quote_id','position'))
    # Legacy totals and committed source rows remain pending: no guessed units or selections.


def downgrade():
    conn=op.get_bind()
    if conn.execute(sa.text("SELECT EXISTS(SELECT 1 FROM cargo_charge_components) OR EXISTS(SELECT 1 FROM cargo_source_rows WHERE disposition <> 'pending') OR EXISTS(SELECT 1 FROM cargo_arrivals WHERE charge_basis IS NOT NULL) OR EXISTS(SELECT 1 FROM quote_snapshots WHERE charge_ngn IS NULL)")).scalar():
        raise RuntimeError('Reviewed components, selections and unknown charges cannot be discarded')
    op.drop_table('cargo_charge_components')
    op.drop_column('cargo_arrivals','charge_basis')
    op.drop_column('cargo_source_rows','reviewed_by')
    op.drop_column('cargo_source_rows','exclusion_reason')
    op.drop_column('cargo_source_rows','disposition')
    op.alter_column('quote_snapshots','charge_ngn',existing_type=sa.Numeric(20,2),nullable=False)
