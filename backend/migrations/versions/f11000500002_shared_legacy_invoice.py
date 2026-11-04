"""Allow evidenced historical group members to reference one shared invoice."""
from alembic import op
import sqlalchemy as sa
revision='f11000500002'
down_revision='f11000500001'
branch_labels=None
depends_on=None

def upgrade():
    op.drop_constraint('tickets_invoice_id_key','tickets',type_='unique')
    op.create_index('ix_tickets_invoice_id','tickets',['invoice_id'])

def downgrade():
    if op.get_bind().execute(sa.text('SELECT EXISTS(SELECT invoice_id FROM tickets WHERE invoice_id IS NOT NULL GROUP BY invoice_id HAVING COUNT(*)>1)')).scalar():
        raise RuntimeError('Refuse downgrade of shared group invoice')
    op.drop_index('ix_tickets_invoice_id',table_name='tickets')
    op.create_unique_constraint('tickets_invoice_id_key','tickets',['invoice_id'])
