"""Bound ordered ticket pages without sorting the complete cargo population."""
from alembic import op
import sqlalchemy as sa

revision='g11000600002'
down_revision='g11000600001'
branch_labels=None
depends_on=None


def upgrade():
    op.create_index('ix_tickets_created_page','tickets',[sa.text('created_at DESC'),'id'])
    op.create_index('ix_tickets_warehouse_created_page','tickets',['warehouse_id',sa.text('created_at DESC'),'id'])


def downgrade():
    op.drop_index('ix_tickets_warehouse_created_page',table_name='tickets')
    op.drop_index('ix_tickets_created_page',table_name='tickets')
