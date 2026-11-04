import hashlib
import json
import os
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, func
from app.models import Command, Session, Attachment, User
from app.common import Actor
from app.attachments import put_file, read_file
from app.settings import protect_private_root
from test_foundation import login


def test_commit_failure_never_sends_success_cookie_or_command(app):
    def fail_commit(db):
        raise RuntimeError('synthetic commit failure')
    with TestClient(app, raise_server_exceptions=False) as client:
        event.listen(app.state.session_factory, 'before_commit', fail_commit)
        try:
            failed = client.post('/api/auth/login', json={'username': 'admin', 'password': 'synthetic-password-123'})
        finally:
            event.remove(app.state.session_factory, 'before_commit', fail_commit)
        assert failed.status_code == 500
        assert 'set-cookie' not in failed.headers
        assert 'csrf_token' not in failed.text
        headers = login(client)
        event.listen(app.state.session_factory, 'before_commit', fail_commit)
        try:
            failed = client.put('/api/admin/settings/billing', headers=headers, json={'expected_version': 0, 'value': {}})
        finally:
            event.remove(app.state.session_factory, 'before_commit', fail_commit)
        assert failed.status_code == 500
        assert '"version"' not in failed.text
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Command)) == 0
        assert db.scalar(select(func.count()).select_from(Session)) == 1  # successful login only


def test_password_fingerprints_are_costly_and_replays_compare_secrets(app):
    with TestClient(app) as client:
        headers = login(client)
        payload = {'username': 'costly', 'password': 'synthetic-secret-password'}
        first = client.post('/api/admin/users', headers=headers, json=payload)
        assert first.status_code == 201
        assert client.post('/api/admin/users', headers=headers, json=payload).json() == first.json()
        assert client.post('/api/admin/users', headers=headers, json={**payload, 'password': 'changed-secret-password'}).status_code == 409
        headers['Idempotency-Key'] = 'reset'
        reset = {'expected_version': 1, 'password': 'reset-secret-password'}
        url = '/api/admin/users/' + first.json()['id']
        done = client.patch(url, headers=headers, json=reset)
        assert done.status_code == 200
        assert client.patch(url, headers=headers, json=reset).json() == done.json()
        assert client.patch(url, headers=headers, json={**reset, 'password': 'different-reset-password'}).status_code == 409
    with app.state.session_factory() as db:
        rows = list(db.scalars(select(Command)))
        assert len(rows) == 2
        for row in rows:
            assert row.request_hash.startswith('argon2id-sha256-v1:$argon2id$')
            assert 'password' not in row.request_hash


def test_old_fingerprints_migrate_without_losing_replay(app):
    from alembic import command
    from app.cli import migration_config
    from app.common import run_command
    config = migration_config()
    config.attributes['database_url'] = app.state.settings.database_url
    command.downgrade(config, 'eec45a507201')
    payload = {'username': 'old-synthetic', 'password': 'old-synthetic-password'}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with app.state.session_factory() as db, db.begin():
        actor_id = db.scalar(select(User.id).where(User.username == 'admin'))
        db.add(Command(actor_id=actor_id, command='users.create', key='legacy', request_hash=digest, result={'id': 'synthetic-result'}))
    command.upgrade(config, 'head')
    with app.state.session_factory() as db, db.begin():
        row = db.scalar(select(Command))
        assert row.request_hash != digest
        def must_not_run():
            raise AssertionError('Migrated retry executed twice')
        assert run_command(db, Actor(actor_id, {'*'}, set()), 'users.create', 'legacy', payload, must_not_run) == {'id': 'synthetic-result'}


def test_every_db_dependency_uses_pre_response_scope(app):
    from app.api.dependencies import get_db
    checked = 0
    def inspect(dependant):
        nonlocal checked
        if dependant.call is get_db:
            assert dependant.scope == 'function'
            checked += 1
        for child in dependant.dependencies:
            inspect(child)
    for route in app.routes:
        if hasattr(route, 'dependant'):
            inspect(route.dependant)
    assert checked > 10


@pytest.mark.skipif(os.name != 'nt', reason='Windows ACL regression')
def test_acl_removes_existing_explicit_grants_on_root_and_descendants(tmp_path):
    root = tmp_path / 'private'
    child = root / 'nested'
    child.mkdir(parents=True)
    file = child / 'existing.txt'
    file.write_text('synthetic')
    for path in (root, child, file):
        subprocess.run(['icacls', str(path), '/grant', '*S-1-1-0:(R)'], check=True, capture_output=True)
    protect_private_root(root)
    script = '''$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$paths=@(Get-Item -LiteralPath $env:MCSTAR_ACL_TEST)+@(Get-ChildItem -LiteralPath $env:MCSTAR_ACL_TEST -Recurse -Force)
foreach($item in $paths){
if($item.PSIsContainer){$acl=[System.IO.Directory]::GetAccessControl($item.FullName)}else{$acl=[System.IO.File]::GetAccessControl($item.FullName)}
if(-not $acl.AreAccessRulesProtected){throw 'Unprotected ACL'}
foreach($rule in $acl.Access){if($rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -ne $sid){throw 'Extra principal'}}}
'''
    checked = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], env={**os.environ, 'MCSTAR_ACL_TEST': str(root)}, capture_output=True)
    assert checked.returncode == 0, 'Existing explicit grants or inheritance remain'


def actor_and_store(app, content):
    with app.state.session_factory() as db, db.begin():
        db.info['settings'] = app.state.settings
        user = db.scalar(select(User).where(User.username == 'operator'))
        actor = Actor(user.id, {'attachments.write'}, set())
        row = put_file(db, actor, content, 'synthetic.bin', 'application/octet-stream', 'user', actor.id)
        return row.id


def intercept_write(monkeypatch, root, callback):
    original = Path.open
    class Wrapped:
        def __init__(self, file): self.file = file
        def __enter__(self): return self
        def __exit__(self, *args): self.file.close()
        def __getattr__(self, name): return getattr(self.file, name)
        def write(self, content): return callback(self.file, content)
    def patched(path, mode='r', *args, **kwargs):
        opened = original(path, mode, *args, **kwargs)
        if mode == 'xb' and path.is_relative_to(root):
            return Wrapped(opened)
        return opened
    monkeypatch.setattr(Path, 'open', patched)


def test_interrupted_attachment_write_leaves_no_partial_final_and_retry_works(app, monkeypatch):
    content = b'original synthetic bytes' * 100
    digest = hashlib.sha256(content).hexdigest()
    final = app.state.settings.private_root / 'objects' / digest[:2] / digest
    def interrupt(file, data):
        file.write(data[:5])
        raise OSError('synthetic interrupted write')
    with monkeypatch.context() as context:
        intercept_write(context, app.state.settings.private_root, interrupt)
        with pytest.raises(OSError):
            actor_and_store(app, content)
    assert not final.exists()
    assert not list(app.state.settings.private_root.rglob('*.tmp'))
    row_id = actor_and_store(app, content)
    with app.state.session_factory() as db:
        assert read_file(app.state.settings, db.get(Attachment, row_id)) == content


def test_simultaneous_identical_objects_publish_only_complete_bytes(app, monkeypatch):
    content = b'concurrent synthetic bytes' * 100
    partial, second_done = Event(), Event()
    def pause_first(file, data):
        if not partial.is_set():
            file.write(data[:7])
            file.flush()
            partial.set()
            assert second_done.wait(10)
            file.write(data[7:])
            return len(data)
        return file.write(data)
    with monkeypatch.context() as context:
        intercept_write(context, app.state.settings.private_root, pause_first)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(actor_and_store, app, content)
            assert partial.wait(10)
            try:
                second_id = actor_and_store(app, content)
            finally:
                second_done.set()
            first_id = first.result()
    assert first_id != second_id
    with app.state.session_factory() as db:
        for row_id in (first_id, second_id):
            assert read_file(app.state.settings, db.get(Attachment, row_id)) == content
