"""Explicit identity adjudication, current legacy applications and recovery watermarks.

Original controls and original source-row fingerprints are never overwritten.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime,timezone
from uuid import uuid4
revision='f11000500006'
down_revision='f11000500005'
branch_labels=None
depends_on=None


def record_columns():
    return [sa.Column('id',sa.String(36),primary_key=True),sa.Column('version',sa.Integer(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False)]


def fk(name,target,unique=False):
    return sa.Column(name,sa.String(36),sa.ForeignKey(target),nullable=False,unique=unique)


def upgrade():
    op.create_table('bank_identity_decisions',fk('left_id','bank_entries.id'),fk('right_id','bank_entries.id'),
        sa.Column('decision',sa.String(20),nullable=False),sa.Column('basis',JSONB(),nullable=False),fk('actor_id','users.id'),
        sa.Column('source',sa.Text(),nullable=False),sa.Column('reason',sa.Text(),nullable=False),*record_columns(),sa.UniqueConstraint('left_id','right_id'))
    op.create_table('legacy_bill_applications',fk('bill_id','legacy_bills.id',True),fk('historical_owner_party_id','parties.id'),
        sa.Column('applied_received',sa.Numeric(20,2),nullable=False),sa.Column('applied_discount',sa.Numeric(20,2),nullable=False),sa.Column('state',sa.String(30),nullable=False),*record_columns())
    op.create_table('legacy_group_revisions',fk('bill_id','legacy_bills.id'),fk('actor_id','users.id'),
        sa.Column('action',sa.String(30),nullable=False),sa.Column('source',sa.Text(),nullable=False),sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('before',JSONB(),nullable=False),sa.Column('after',JSONB(),nullable=False),sa.Column('preview',JSONB(),nullable=False),*record_columns())
    op.create_index('ix_legacy_group_revisions_bill_id','legacy_group_revisions',['bill_id'])
    op.create_table('recovery_checkpoints',fk('actor_id','users.id'),sa.Column('source',sa.Text(),nullable=False),sa.Column('cutoff',sa.DateTime(timezone=True),nullable=False),
        sa.Column('state',sa.String(30),nullable=False),sa.Column('facts',JSONB(),nullable=False),sa.Column('writer',JSONB(),nullable=False),*record_columns())
    # Existing resolved bills retain exactly their original applied control. This
    # additive backfill is distinct from the separately reviewed parser-v2 plan.
    bind=op.get_bind();now=datetime.now(timezone.utc)
    for b in bind.execute(sa.text('SELECT b.id,b.received,b.discount,i.party_id FROM legacy_bills b JOIN invoices i ON i.id=b.native_id')).mappings():
        bind.execute(sa.text("INSERT INTO legacy_bill_applications(id,version,created_at,updated_at,bill_id,historical_owner_party_id,applied_received,applied_discount,state) VALUES(:id,1,:now,:now,:bill,:owner,:received,:discount,'active')"),dict(id=str(uuid4()),now=now,bill=b['id'],owner=b['party_id'],received=b['received'],discount=b['discount']))


def downgrade():
    bind=op.get_bind()
    for table in ('bank_identity_decisions','legacy_group_revisions','recovery_checkpoints'):
        if bind.execute(sa.text(f'SELECT EXISTS(SELECT 1 FROM {table})')).scalar():raise RuntimeError('Refuse loss of reviewed identity/group/recovery facts')
    changed=bind.execute(sa.text("SELECT EXISTS(SELECT 1 FROM legacy_bill_applications a JOIN legacy_bills b ON b.id=a.bill_id JOIN invoices i ON i.id=b.native_id WHERE a.state<>'active' OR a.applied_received<>b.received OR a.applied_discount<>b.discount OR a.historical_owner_party_id<>i.party_id)")).scalar()
    if changed:raise RuntimeError('Refuse loss of current/frozen historical application')
    for table in ('recovery_checkpoints','legacy_group_revisions','legacy_bill_applications','bank_identity_decisions'):op.drop_table(table)
