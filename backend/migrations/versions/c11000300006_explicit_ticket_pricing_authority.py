"""Keep explicit total overrides distinct from effective quote authority."""
from alembic import op
import sqlalchemy as sa
revision='c11000300006'
down_revision='c11000300005'
branch_labels=None
depends_on=None


def upgrade():
    # Preserve old totals, AR and quote references; unknown prior authority needs review.
    op.add_column('tickets',sa.Column('pricing_authority',sa.String(12),nullable=False,server_default='review'))
    op.alter_column('tickets','pricing_authority',server_default=None)
    op.create_check_constraint('ck_ticket_pricing_authority','tickets',"pricing_authority IN ('quote','totals','pending','review')")


def downgrade():
    if op.get_bind().execute(sa.text('SELECT EXISTS(SELECT 1 FROM tickets)')).scalar():
        raise RuntimeError('Cannot discard pricing authority of existing tickets')
    op.drop_constraint('ck_ticket_pricing_authority','tickets',type_='check')
    op.drop_column('tickets','pricing_authority')
