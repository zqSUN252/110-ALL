"""Bind delayed assistant replies to their persisted, authorized tool results."""
from alembic import op
import sqlalchemy as sa

revision='e11000700002'
down_revision='e11000700001'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('channel_deliveries',sa.Column('assistant_turn_id',sa.String(36),nullable=True))
    op.add_column('channel_deliveries',sa.Column('assistant_result_hash',sa.String(64),nullable=True))
    op.create_foreign_key('fk_delivery_assistant_turn','channel_deliveries','dialogue_turns',['assistant_turn_id'],['id'])
    op.create_check_constraint('ck_delivery_assistant_provenance','channel_deliveries',"(assistant_turn_id IS NULL AND assistant_result_hash IS NULL) OR (assistant_turn_id IS NOT NULL AND assistant_result_hash IS NOT NULL AND text = '' AND artifact_id IS NULL)")
    # Do not reconstruct authority from cached text or replay old replies. Keep facts/history.
    op.execute("""UPDATE channel_deliveries SET state='blocked', version=version+1
        WHERE state='pending' AND id IN
        (SELECT result->>'id' FROM commands WHERE command='channel.delivery.queue' AND key LIKE 'assistant-reply:%')""")


def downgrade():
    if op.get_bind().execute(sa.text('SELECT EXISTS (SELECT 1 FROM channel_deliveries WHERE assistant_turn_id IS NOT NULL OR assistant_result_hash IS NOT NULL)')).scalar():
        raise RuntimeError('Refuse to discard assistant reply provenance')
    op.drop_constraint('ck_delivery_assistant_provenance','channel_deliveries',type_='check')
    op.drop_constraint('fk_delivery_assistant_turn','channel_deliveries',type_='foreignkey')
    op.drop_column('channel_deliveries','assistant_result_hash')
    op.drop_column('channel_deliveries','assistant_turn_id')
