"""One occupied bank claim may evidence multiple historical bill controls."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='f11000500005'
down_revision='f11000500004'
branch_labels=None
depends_on=None

def record_columns():
    return [sa.Column('id',sa.String(36),primary_key=True),sa.Column('version',sa.Integer(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False)]

def upgrade():
    op.create_table('legacy_bank_evidence',
        sa.Column('claim_id',sa.String(36),sa.ForeignKey('bank_claims.id'),nullable=False,unique=True),
        sa.Column('account_id',sa.String(36),sa.ForeignKey('bank_accounts.id'),nullable=False),
        sa.Column('control_owner_party_id',sa.String(36),sa.ForeignKey('parties.id'),nullable=False),
        sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('scope',sa.String(12),nullable=False),sa.Column('state',sa.String(30),nullable=False),
        sa.Column('reason',sa.Text(),nullable=False),sa.Column('source',sa.Text(),nullable=False),
        sa.Column('basis',JSONB(),nullable=False),sa.Column('cross_party',JSONB(),nullable=True),*record_columns())
    op.create_table('legacy_bank_coverage',
        sa.Column('evidence_id',sa.String(36),sa.ForeignKey('legacy_bank_evidence.id'),nullable=False),
        sa.Column('bill_id',sa.String(36),sa.ForeignKey('legacy_bills.id'),nullable=False),
        sa.Column('source_version',sa.Integer(),nullable=False),sa.Column('amount_ngn',sa.Numeric(20,2),nullable=False),
        sa.Column('source',sa.Text(),nullable=False),*record_columns(),sa.UniqueConstraint('evidence_id','bill_id'))
    op.create_index('ix_legacy_bank_coverage_evidence_id','legacy_bank_coverage',['evidence_id'])
    op.create_index('ix_legacy_bank_coverage_bill_id','legacy_bank_coverage',['bill_id'])

def downgrade():
    if op.get_bind().execute(sa.text('SELECT EXISTS(SELECT 1 FROM legacy_bank_evidence)')).scalar():raise RuntimeError('Refuse loss of historical bank evidence')
    op.drop_table('legacy_bank_coverage');op.drop_table('legacy_bank_evidence')
