import os
from pathlib import Path
import subprocess
from alembic import command
from sqlalchemy import inspect, text, select
from app.cli import migration_config
from app.models import Grant, User


def test_frozen_migration_round_trip(app):
    engine = app.state.engine
    config = migration_config()
    config.attributes['database_url'] = app.state.settings.database_url
    command.downgrade(config, 'base')
    assert inspect(engine).get_table_names() == ['alembic_version']
    command.upgrade(config, 'head')
    assert {'users', 'grants', 'sessions', 'attachments', 'commands', 'audit_events', 'jobs', 'outbox'} <= set(inspect(engine).get_table_names())
    command.check(config)


def test_cli_initial_admin_is_one_time(app):
    # A fresh isolated schema, never modifies the developer's public schema/accounts.
    config = migration_config()
    config.attributes['database_url'] = app.state.settings.database_url
    command.downgrade(config, 'base')
    import sys
    env = {**os.environ, 'MCSTAR_DATABASE_URL': app.state.settings.database_url,
        'MCSTAR_ADMIN_PASSWORD': 'synthetic-cli-password', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    migrated = subprocess.run([sys.executable, '-m', 'app.cli', 'init-db'], env=env, capture_output=True, text=True)
    assert migrated.returncode == 0, migrated.stderr
    args = [sys.executable, '-m', 'app.cli', 'create-admin', '--username', 'cli-admin']
    first = subprocess.run(args, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run(args, env=env, capture_output=True, text=True)
    assert second.returncode != 0
    assert 'already exists' in second.stderr
    assert 'synthetic-cli-password' not in first.stdout + first.stderr + second.stdout + second.stderr
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        result = client.post('/api/auth/login', json={'username': 'cli-admin', 'password': 'synthetic-cli-password'})
        assert result.status_code == 200
        assert client.get('/api/admin/users').status_code == 200
