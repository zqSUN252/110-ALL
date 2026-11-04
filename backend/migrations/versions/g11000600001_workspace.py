"""Typed announcements for the operator workspace."""
from alembic import op
import sqlalchemy as sa

revision = 'g11000600001'
down_revision = 'f11000500006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('announcements',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('title_zh', sa.String(200), nullable=False),
        sa.Column('title_en', sa.String(200), nullable=False),
        sa.Column('body_zh', sa.Text(), nullable=False),
        sa.Column('body_en', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('actor_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT EXISTS(SELECT 1 FROM announcements)')).scalar():
        raise RuntimeError('Refusing to discard announcements')
    op.drop_table('announcements')
