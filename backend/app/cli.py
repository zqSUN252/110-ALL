import argparse
import getpass
import os
from pathlib import Path
from alembic.config import Config
from alembic import command
from argon2 import PasswordHasher
from sqlalchemy import select, text
from app.settings import load_settings
from app.db import make_engine, session_factory
from app.models import User, Grant
from app.common import Actor, audit


def migration_config():
    return Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))


def main():
    parser = argparse.ArgumentParser(description='Controlled local MC STAR administration')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init-db')
    admin = commands.add_parser('create-admin')
    admin.add_argument('--username', required=True)
    args = parser.parse_args()
    settings = load_settings()
    if args.command == 'init-db':
        config = migration_config()
        config.attributes['database_url'] = settings.database_url
        command.upgrade(config, 'head')
        print('Database migrated to head.')
        return
    password = os.environ.pop('MCSTAR_ADMIN_PASSWORD', None) or getpass.getpass('New administrator password: ')
    if len(password) < 12:
        parser.error('Password must contain at least 12 characters')
    engine = make_engine(settings.database_url)
    with session_factory(engine)() as db, db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('initial-admin', 0))"))
        if db.scalar(select(Grant.id).where(Grant.capability == '*').limit(1)):
            parser.error('Initial administrator already exists; manage accounts through authenticated API')
        if db.scalar(select(User.id).where(User.username == args.username)):
            parser.error('Username already exists; no account was overwritten')
        user = User(username=args.username, password_hash=PasswordHasher().hash(password))
        db.add(user)
        db.flush()
        db.add(Grant(user_id=user.id, capability='*'))
        audit(db, Actor(user.id, {'*'}, set()), 'users.bootstrap', 'user', user.id, None, {'username': user.username})
    engine.dispose()
    print('Initial administrator created. No password was printed or saved in source.')


if __name__ == '__main__':
    main()
