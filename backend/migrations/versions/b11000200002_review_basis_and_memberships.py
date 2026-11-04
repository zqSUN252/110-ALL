"""Reviewed bank basis and explicit warehouse participation.

Existing home warehouses become explicit, source-labeled memberships only.
Unversioned old claims are conservatively frozen without changing cash.
"""
from alembic import op
import sqlalchemy as sa
revision='b11000200002'
down_revision='b11000200001'
branch_labels=None
depends_on=None


def upgrade():
    for table,owner,fk in [('party_warehouses','party_id','parties'),('bank_account_warehouses','account_id','bank_accounts')]:
        op.create_table(table,
            sa.Column('id',sa.String(36),primary_key=True),sa.Column('version',sa.Integer(),nullable=False),
            sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
            sa.Column(owner,sa.String(36),sa.ForeignKey(fk+'.id'),nullable=False),
            sa.Column('warehouse_id',sa.String(36),sa.ForeignKey('warehouses.id'),nullable=False),
            sa.Column('active',sa.Boolean(),nullable=False),sa.Column('source',sa.Text(),nullable=False),
            sa.UniqueConstraint(owner,'warehouse_id'))
        op.create_index('ix_'+table+'_'+owner,table,[owner])
        op.create_index('ix_'+table+'_warehouse_id',table,['warehouse_id'])
        op.execute(sa.text(f"INSERT INTO {table} (id,version,created_at,updated_at,{owner},warehouse_id,active,source) SELECT gen_random_uuid()::text,1,now(),now(),id,warehouse_id,true,'migration: explicit prior home warehouse' FROM {fk}"))
    op.add_column('bank_claims',sa.Column('reviewed_entry_version',sa.Integer(),nullable=True))
    op.add_column('bank_claims',sa.Column('reviewed_bank_revision',sa.Integer(),nullable=True))
    op.add_column('bank_claims',sa.Column('reviewed_basis_hash',sa.String(64),nullable=True))
    op.create_table('cross_party_authorizations',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('payment_id',sa.String(36),sa.ForeignKey('payments.id'),nullable=False),
        sa.Column('invoice_id',sa.String(36),sa.ForeignKey('invoices.id'),nullable=False),
        sa.Column('beneficiary_party_id',sa.String(36),sa.ForeignKey('parties.id'),nullable=False),
        sa.Column('amount_ngn',sa.Numeric(20,2),nullable=False),sa.Column('source',sa.Text(),nullable=False),
        sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),sa.CheckConstraint('amount_ngn > 0'))
    op.create_index('ix_cross_party_authorizations_payment_id','cross_party_authorizations',['payment_id'])
    op.execute("INSERT INTO audit_events(id,version,created_at,updated_at,action,entity_type,entity_id,before,after) SELECT gen_random_uuid()::text,1,now(),now(),'bank.basis_migration','bank_claim',id,jsonb_build_object('state',state),jsonb_build_object('state','disputed','reason','reviewed bank basis missing') FROM bank_claims WHERE state <> 'released'")
    op.execute("INSERT INTO finance_events(id,version,created_at,updated_at,payment_id,kind) SELECT gen_random_uuid()::text,1,now(),now(),payment_id,'evidence_disputed' FROM bank_claims WHERE state <> 'released' AND payment_id IS NOT NULL")
    op.execute("UPDATE payments SET bank_state='disputed',version=version+1,updated_at=now() WHERE id IN (SELECT payment_id FROM bank_claims WHERE state <> 'released')")
    op.execute("UPDATE evidence_cases SET state='disputed',version=version+1,updated_at=now() WHERE id IN (SELECT case_id FROM bank_claims WHERE state <> 'released')")
    op.execute("UPDATE bank_claims SET state='disputed',version=version+1,updated_at=now() WHERE state <> 'released'")


def downgrade():
    # Do not discard non-home participation which the old version cannot represent.
    bind=op.get_bind()
    if bind.scalar(sa.text('SELECT count(*) FROM cross_party_authorizations')):
        raise RuntimeError('Cannot downgrade source-backed cross-party financial authorizations')
    if bind.scalar(sa.text("SELECT count(*) FROM bank_claims WHERE state <> 'released' AND reviewed_basis_hash IS NOT NULL")):
        raise RuntimeError('Cannot downgrade and discard an active reviewed bank basis')
    for table,owner,fk in [('party_warehouses','party_id','parties'),('bank_account_warehouses','account_id','bank_accounts')]:
        if bind.scalar(sa.text(f'SELECT count(*) FROM {table} m JOIN {fk} o ON m.{owner}=o.id WHERE m.warehouse_id<>o.warehouse_id OR NOT m.active')):
            raise RuntimeError('Cannot downgrade explicit non-home or revoked memberships; preserve and review the data first')
    for column in ('reviewed_basis_hash','reviewed_bank_revision','reviewed_entry_version'):op.drop_column('bank_claims',column)
    op.drop_table('cross_party_authorizations')
    op.drop_table('bank_account_warehouses');op.drop_table('party_warehouses')
