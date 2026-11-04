import io,json
from zipfile import ZipFile,ZIP_STORED
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.models import User,Grant
from test_migration import sample_source,applied


def test_global_legacy_http_scope_and_admin_input_are_separate(app):
    applied(app)
    with app.state.session_factory() as db,db.begin():
        uid=db.scalar(select(User.id).where(User.username=='operator'))
        db.add_all([Grant(user_id=uid,capability=c) for c in ('legacy.global_read','finance.read')])
    with TestClient(app) as client:
        assert client.get('/api/legacy/bills').status_code==401
        client.post('/api/auth/login',json={'username':'operator','password':'synthetic-password-123'})
        result=client.get('/api/legacy/bills');assert result.status_code==200 and result.json()['total']==1
        assert client.get('/api/migration/batches').status_code==403
        client.post('/api/auth/login',json={'username':'other','password':'synthetic-password-123'})
        assert client.get('/api/legacy/bills').status_code==403


def test_private_source_archive_above_normal_attachment_limit_and_worker(app):
    output=io.BytesIO()
    with ZipFile(output,'w',compression=ZIP_STORED) as z:
        z.writestr('mcstar_pkg/data/data.json',json.dumps(sample_source()))
        z.writestr('mcstar_pkg/prompts/用户提示词全记录.md','synthetic no configuration')
        z.writestr('unused.bin',b'0'*(21*1024*1024))
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        headers={'X-CSRF-Token':auth['csrf_token'],'Content-Type':'application/zip'}
        response=client.post('/api/migration/sources',content=output.getvalue(),headers=headers)
        assert response.status_code==200,response.text
        source=response.json();assert source['size_bytes']>20*1024*1024
        plan=client.get('/api/migration/sources/'+source['id']+'/plan',params={'dataset':'http-source'}).json()
        response=client.post('/api/migration/sources/'+source['id']+'/apply',json={'dataset':'http-source','plan_hash':plan['plan_hash']},headers={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'source-apply'})
        assert response.status_code==200,response.text
        queued=response.json()
        from app.migration.worker import run_once
        result=run_once(app.state.session_factory,app.state.settings);assert result['state']=='complete'
        assert client.get('/api/migration/jobs/'+queued['id']).json()['result']['created']>0
        assert client.get('/api/legacy/bills').json()['total']==1
        assert client.get('/api/migration/sources/'+source['id']).status_code==404
        assert client.post('/api/migration/sources',content=b'not a zip',headers=headers).status_code==400
        from app.migration.backup import backup
        backup(app.state.settings,app.state.settings.private_root/'backup-test')
        assert not list((app.state.settings.private_root/'backup-test').rglob('*.zip'))


def test_database_unreachable_health_is_bounded_and_redacted(app):
    from dataclasses import replace
    from app.main import create_app
    from time import monotonic
    # Closed localhost port; no remote service or narrow fake database assertion.
    import socket
    with socket.socket() as unavailable:
        unavailable.bind(('127.0.0.1',0));port=unavailable.getsockname()[1]
        target=create_app(replace(app.state.settings,database_url=f'postgresql+psycopg://secret-user:secret-password@127.0.0.1:{port}/secret-db'))
        started=monotonic()
        try:
            with TestClient(target) as client:
                response=client.get('/health')
            assert response.status_code==503 and response.json()=={'status':'database_unreachable'}
            assert monotonic()-started<7
            assert 'secret' not in response.text
        finally:target.state.engine.dispose()
