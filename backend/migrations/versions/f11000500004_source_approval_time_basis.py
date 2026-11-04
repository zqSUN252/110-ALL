"""Distinguish recorded source timestamps from verified original approval times."""
from alembic import op
import sqlalchemy as sa
revision='f11000500004'
down_revision='f11000500003'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('legacy_payment_observations',sa.Column('approval_time_basis',sa.String(60),nullable=True))

def downgrade():
    if op.get_bind().execute(sa.text('SELECT EXISTS(SELECT 1 FROM legacy_payment_observations WHERE approval_time_basis IS NOT NULL)')).scalar():raise RuntimeError('Refuse loss of source approval time semantics')
    op.drop_column('legacy_payment_observations','approval_time_basis')
