"""Retain the exact document cancellation boundary; never infer old void members."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision='h11000800001'
down_revision='g11000600002'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('packing_documents',sa.Column('void_members',postgresql.JSONB(),nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM packing_documents WHERE void_members IS NOT NULL LIMIT 1')).first():
        raise RuntimeError('Cannot discard nonempty document cancellation membership')
    op.drop_column('packing_documents','void_members')
