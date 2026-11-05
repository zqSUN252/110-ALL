"""Native billing identities and immutable membership/financial decisions. No inferred backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='h11000800003'
down_revision='h11000800002'
branch_labels=None
depends_on=None


def record():
    return [sa.Column('id',sa.String(36),primary_key=True),sa.Column('version',sa.Integer(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False)]


def upgrade():
    op.create_table('native_billing_groups',*record(),
        sa.Column('document_id',sa.String(36),sa.ForeignKey('packing_documents.id'),nullable=False),
        sa.Column('warehouse_id',sa.String(36),sa.ForeignKey('warehouses.id'),nullable=False),
        sa.Column('party_id',sa.String(36),sa.ForeignKey('parties.id')),
        sa.Column('invoice_id',sa.String(36),sa.ForeignKey('invoices.id'),unique=True),sa.Column('state',sa.String(20),nullable=False))
    op.create_index('ix_native_billing_groups_document_id','native_billing_groups',['document_id'])
    op.add_column('tickets',sa.Column('billing_group_id',sa.String(36),sa.ForeignKey('native_billing_groups.id')))
    op.create_index('ix_tickets_billing_group_id','tickets',['billing_group_id'])
    op.create_table('native_group_revisions',*record(),
        sa.Column('document_id',sa.String(36),sa.ForeignKey('packing_documents.id'),nullable=False),
        sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('reason',sa.Text(),nullable=False),sa.Column('before',JSONB,nullable=False),sa.Column('after',JSONB,nullable=False))
    op.create_index('ix_native_group_revisions_document_id','native_group_revisions',['document_id'])


def downgrade():
    bind=op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM native_billing_groups LIMIT 1')).first() or bind.execute(sa.text('SELECT 1 FROM native_group_revisions LIMIT 1')).first():raise RuntimeError('Cannot discard nonempty native billing identities and financial history')
    op.drop_table('native_group_revisions');op.drop_column('tickets','billing_group_id');op.drop_table('native_billing_groups')
