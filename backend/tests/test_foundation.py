from fastapi.testclient import TestClient
from app.main import create_app
from app.settings import load_settings


def login(client, username='admin', password='synthetic-password-123'):
    result = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert result.status_code == 200, result.text
    return {'X-CSRF-Token': result.json()['csrf_token'], 'Idempotency-Key': 'test-key'}


def test_anonymous_and_admin_login(app):
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/api/me').status_code == 401
        assert client.get('/api/admin/users').status_code == 401
        headers = login(client)
        assert client.get('/api/me').json()['username'] == 'admin'
        assert client.get('/api/admin/users').status_code == 200
        assert client.post('/api/auth/logout').status_code == 403
        assert client.post('/api/auth/logout', headers=headers).status_code == 200
        assert client.get('/api/me').status_code == 401


def test_grants_change_on_next_request(app):
    from app.models import Warehouse
    with app.state.session_factory() as db,db.begin():
        warehouse=Warehouse(name='Synthetic scope warehouse');db.add(warehouse);db.flush();warehouse_id=warehouse.id
    with TestClient(app) as admin, TestClient(app) as low:
        headers = login(admin)
        login(low, 'operator')
        assert low.get('/api/admin/users').status_code == 403
        user = next(u for u in admin.get('/api/admin/users').json() if u['username'] == 'operator')
        response = admin.put(f"/api/admin/users/{user['id']}/grants", headers=headers,
            json={'expected_version': user['version'], 'capabilities': ['users.read'], 'warehouse_ids': [warehouse_id]})
        assert response.status_code == 200, response.text
        assert low.get('/api/admin/users').status_code == 200
        assert low.get('/api/me').json()['warehouse_ids'] == [warehouse_id]


def test_version_csrf_idempotency_and_audit(app):
    with TestClient(app) as client:
        headers = login(client)
        payload = {'value': {'currency': 'NGN'}, 'expected_version': 0}
        assert client.put('/api/admin/settings/billing', json=payload).status_code == 403
        first = client.put('/api/admin/settings/billing', json=payload, headers=headers)
        assert first.status_code == 200, first.text
        assert client.put('/api/admin/settings/billing', json=payload, headers=headers).json() == first.json()
        assert client.put('/api/admin/settings/billing', json={**payload, 'value': {}}, headers=headers).status_code == 409
        assert client.put('/api/admin/settings/billing', json=payload,
            headers={**headers, 'Idempotency-Key': 'stale'}).status_code == 409
        events = client.get('/api/admin/audit').json()
        assert len([e for e in events if e['action'] == 'settings.update']) == 1


def test_private_attachment_owner_acl(app):
    with TestClient(app) as owner, TestClient(app) as other, TestClient(app) as anon:
        headers = login(owner, 'operator')
        login(other, 'other')
        uploaded = owner.post('/api/attachments', files={'file': ('receipt.txt', b'original bytes', 'text/plain')},
            data={'owner_type': 'user', 'owner_id': owner.get('/api/me').json()['id']}, headers=headers)
        assert uploaded.status_code == 201, uploaded.text
        attachment_id = uploaded.json()['id']
        assert owner.get(f'/api/attachments/{attachment_id}').content == b'original bytes'
        assert anon.get(f'/api/attachments/{attachment_id}').status_code == 401
        assert other.get(f'/api/attachments/{attachment_id}').status_code == 403


def test_user_crud_and_password_never_echoed(app):
    with TestClient(app) as client:
        headers = login(client)
        invalid = client.post('/api/admin/users', json={'username': 'new', 'password': 'TOO-SHORT'}, headers=headers)
        assert invalid.status_code == 422
        assert 'TOO-SHORT' not in invalid.text
        created = client.post('/api/admin/users', json={'username': 'new', 'password': 'synthetic-new-password'}, headers=headers)
        assert created.status_code == 201, created.text
        user = created.json()
        assert 'password' not in created.text
        headers['Idempotency-Key'] = 'update'
        updated = client.patch(f"/api/admin/users/{user['id']}", json={'expected_version': 1, 'username': 'renamed'}, headers=headers)
        assert updated.status_code == 200
        headers['Idempotency-Key'] = 'delete'
        assert client.request('DELETE', f"/api/admin/users/{user['id']}", json={'expected_version': 2}, headers=headers).json()['active'] is False
        assert any(u['id'] == user['id'] for u in client.get('/api/admin/users').json())


def test_failed_command_rolls_back_all_changes(app):
    from app.common import Actor, DomainError, run_command, audit
    from app.models import AppSetting, User, Command, AuditEvent
    from sqlalchemy import select
    import pytest
    with app.state.session_factory() as db:
        actor = Actor(db.scalar(select(User.id).where(User.username == 'admin')), {'*'}, set())
    with pytest.raises(DomainError), app.state.session_factory() as db, db.begin():
        def failing():
            db.add(AppSetting(key='rollback', value={'amount': '1.00'}))
            audit(db, actor, 'test.rollback', 'setting', 'test', None, {})
            db.flush()
            raise DomainError('intentional', 'Synthetic rollback')
        run_command(db, actor, 'test.rollback', 'key', {}, failing)
    with app.state.session_factory() as db:
        assert db.scalar(select(AppSetting).where(AppSetting.key == 'rollback')) is None
        assert db.scalar(select(Command).where(Command.command == 'test.rollback')) is None
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == 'test.rollback')) is None


def test_concurrent_idempotent_command_decimal_result(app):
    from concurrent.futures import ThreadPoolExecutor
    from decimal import Decimal
    from app.common import Actor, run_command
    from app.models import User, AppSetting, Command
    from sqlalchemy import select, func
    with app.state.session_factory() as db:
        actor = Actor(db.scalar(select(User.id).where(User.username == 'admin')), {'*'}, set())
    def work(_):
        with app.state.session_factory() as db, db.begin():
            def apply():
                db.add(AppSetting(key='once', value={}))
                return {'amount': Decimal('123.40')}
            return run_command(db, actor, 'concurrent', 'shared', {'amount': Decimal('123.40')}, apply)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(work, range(2)))
    assert results == [{'amount': '123.40'}, {'amount': '123.40'}]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Command).where(Command.command == 'concurrent')) == 1


def test_attachment_integrity_and_owner_spoof_rejected(app):
    from app.models import Attachment
    with TestClient(app) as client:
        headers = login(client, 'operator')
        body = {'owner_type': 'user', 'owner_id': 'someone-else'}
        assert client.post('/api/attachments', files={'file': ('a.txt', b'a')}, data=body, headers=headers).status_code == 403
        body['owner_id'] = client.get('/api/me').json()['id']
        uploaded = client.post('/api/attachments', files={'file': ('../../a.txt', b'a')}, data=body, headers=headers)
        assert uploaded.status_code == 201
        assert uploaded.json()['filename'] == 'a.txt'
        with app.state.session_factory() as db:
            row = db.get(Attachment, uploaded.json()['id'])
            (app.state.settings.private_root / row.object_path).write_bytes(b'corrupt')
        assert client.get('/api/attachments/' + uploaded.json()['id']).status_code == 500


def test_runtime_switches_are_not_web_editable(app):
    with TestClient(app) as client:
        headers = login(client)
        assert client.put('/api/admin/settings/sending_enabled', json={'expected_version': 0, 'value': {'enabled': True}}, headers=headers).status_code == 422
        response = client.put('/api/admin/settings/billing', json={'expected_version': 0, 'value': {'api_key': 'synthetic-sensitive'}}, headers=headers)
        assert response.status_code == 422
        assert 'synthetic-sensitive' not in response.text
