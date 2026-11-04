from dataclasses import replace
from uuid import uuid4
import pytest
from alembic import command
from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.engine import make_url
from app.settings import load_settings
from app.db import make_engine, session_factory
from app.main import create_app
from app.cli import migration_config
from app.models import User, Grant


@pytest.fixture
def app(tmp_path):
    settings = load_settings()  # configuration absence is a failure, never a skip
    schema = 'test_' + uuid4().hex
    master = make_engine(settings.database_url)
    with master.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    url = make_url(settings.database_url).update_query_dict({'options': f'-csearch_path={schema}'})
    scoped = replace(settings, database_url=url.render_as_string(hide_password=False), private_root=tmp_path, secure_cookies=False)
    app = None
    try:
        app = create_app(scoped)
        config = migration_config()
        config.attributes['database_url'] = scoped.database_url
        command.upgrade(config, 'head')
        with app.state.session_factory() as db, db.begin():
            for username, caps in [('admin', ['*']), ('operator', ['attachments.read', 'attachments.write']), ('other', ['attachments.read', 'attachments.write'])]:
                user = User(username=username, password_hash=PasswordHasher().hash('synthetic-password-123'))
                db.add(user)
                db.flush()
                db.add_all([Grant(user_id=user.id, capability=cap) for cap in caps])
        yield app
    finally:
        if app is not None:
            app.state.engine.dispose()
        with master.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        master.dispose()
