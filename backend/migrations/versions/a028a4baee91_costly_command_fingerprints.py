"""Replace fast command fingerprints with versioned Argon2id verifiers.

Legacy SHA256 digests can be cost-hashed without knowing original requests.
"""
from alembic import op
from argon2 import PasswordHasher
import sqlalchemy as sa

revision = 'a028a4baee91'
down_revision = 'eec45a507201'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('commands', 'request_hash', type_=sa.Text(), existing_type=sa.String(64), existing_nullable=False)
    connection = op.get_bind()
    rows = connection.execute(sa.text('SELECT id, request_hash FROM commands')).fetchall()
    hasher = PasswordHasher()
    for row in rows:
        connection.execute(sa.text('UPDATE commands SET request_hash=:fingerprint WHERE id=:id'),
            {'id': row.id, 'fingerprint': 'argon2id-sha256-v1:' + hasher.hash(row.request_hash)})


def downgrade():
    # An empty schema can be reverted for tests. Existing verifiers are deliberately
    # irreversible: never delete commands or recreate the weaker password oracle.
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM commands')):
        raise RuntimeError('Cannot downgrade nonempty command fingerprints; restore a controlled backup if required')
    op.alter_column('commands', 'request_hash', type_=sa.String(64), existing_type=sa.Text(), existing_nullable=False)
