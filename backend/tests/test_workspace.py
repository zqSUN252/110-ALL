from fastapi.testclient import TestClient


def login(client, name='admin'):
    return client.post('/api/auth/login', json={'username': name, 'password': 'synthetic-password-123'}).json()['csrf_token']


def test_reload_session_restores_csrf_without_exposing_session(app):
    with TestClient(app) as client:
        login(client)
        response = client.get('/api/auth/session')
        assert response.status_code == 200
        data = response.json()
        assert data['user']['username'] == 'admin'
        assert 'token' not in data and 'session' not in data
        headers = {'X-CSRF-Token': data['csrf_token'], 'Idempotency-Key': 'reload-warehouse'}
        assert client.post('/api/warehouses', headers=headers, json={'name': 'SYNTHETIC reload warehouse'}).status_code == 200
        assert client.post('/api/warehouses', json={'name': 'csrf negative'}).status_code == 403
        assert 'no-store' in response.headers['cache-control']


def test_announcement_version_permission_and_public_profile(app):
    with TestClient(app) as client:
        csrf = login(client)
        headers = {'X-CSRF-Token': csrf, 'Idempotency-Key': 'announcement-create'}
        response = client.post('/api/announcements', headers=headers, json={'title_zh':'合成公告','title_en':'Synthetic notice','body_zh':'测试内容','body_en':'Test content','active':True})
        assert response.status_code == 200
        row = response.json()
        assert client.get('/api/announcements').json()['items'][0]['id'] == row['id']
        headers['Idempotency-Key'] = 'announcement-stale'
        assert client.patch('/api/announcements/'+row['id'], headers=headers, json={'expected_version':99,'active':False}).status_code == 409
        csrf = login(client, 'operator')
        headers = {'X-CSRF-Token':csrf,'Idempotency-Key':'announcement-denied'}
        assert client.patch('/api/announcements/'+row['id'],headers=headers,json={'expected_version':1,'active':False}).status_code == 403
        assert client.get('/api/workspace/search',params={'query':'synthetic'}).json()['items'] == []
        assert client.get('/api/workspace/summary').json()=={'counts':{}}
        assert len([r for r in app.routes if getattr(r,'path',None)=='/api/public/profile']) == 1


def test_workspace_totals_cover_all_rows_and_respect_membership(app):
    from sqlalchemy import select
    from app.models import User,Grant,WarehouseScope,Invoice
    with TestClient(app) as client:
        csrf=login(client);sequence=0
        def post(path,body):
            nonlocal sequence
            sequence+=1
            r=client.post('/api'+path,json=body,headers={'X-CSRF-Token':csrf,'Idempotency-Key':'workspace-'+str(sequence)})
            assert r.status_code==200,r.text
            return r.json()
        origin=post('/warehouses',{'name':'Synthetic origin'});shared=post('/warehouses',{'name':'Synthetic shared'})
        party=post('/parties',{'name':'Synthetic payer','warehouse_id':origin['id']})
        account=post('/bank/accounts',{'warehouse_id':origin['id'],'bank_name':'Synthetic bank','account_number':'1111','currency':'NGN'})
        payment=post('/payments',{'party_id':party['id'],'warehouse_id':origin['id'],'amount':'10','currency':'NGN','paid_on':'2026-11-01'})
        collection=client.get('/api/finance/records/payments',params={'warehouse_id':origin['id']})
        assert collection.status_code==200
        assert [r['id'] for r in collection.json()['items']]==[payment['id']]
        assert client.get('/api/finance/records/invoices',params={'warehouse_id':origin['id']}).status_code==200
        post('/parties/'+party['id']+'/warehouses',{'expected_version':party['version'],'warehouse_id':shared['id'],'active':True,'source':'Explicit synthetic membership'})
        post('/bank/accounts/'+account['id']+'/warehouses',{'expected_version':account['version'],'warehouse_id':shared['id'],'active':True,'source':'Explicit synthetic membership'})
        with app.state.session_factory() as db,db.begin():
            for i in range(53):
                db.add(Invoice(party_id=party['id'],warehouse_id=shared['id'],kind='AR',scope='live',reference='SYN-'+str(i),amount_ngn='10.01',state='active',uploaded_on=__import__('datetime').date(2026,11,1),due_on=__import__('datetime').date(2026,11,15)))
            user=db.scalar(select(User).where(User.username=='operator'))
            for cap in ['bank.read','parties.read','finance.read']:db.add(Grant(user_id=user.id,capability=cap))
            db.add(WarehouseScope(user_id=user.id,warehouse_id=shared['id']))
        csrf=login(client,'operator')
        result=client.get('/api/workspace/finance').json()
        assert len(result['items'])==50 and result['total']==53
        assert result['totals']['amount_ngn']=='530.53'
        assert client.get('/api/bank/accounts/'+account['id']).status_code==200
        assert [r['id'] for r in client.get('/api/workspace/options?kind=accounts').json()['items']]==[account['id']]
        assert [r['id'] for r in client.get('/api/workspace/options?kind=parties').json()['items']]==[party['id']]
        found=client.get('/api/workspace/search',params={'query':'Synthetic payer'}).json()['items']
        assert any(r['kind']=='parties' and r['id']==party['id'] for r in found)
        assert any(r['kind']=='invoices' for r in found)
        assert client.get('/api/workspace/finance',params={'warehouse_id':origin['id']}).status_code==403
        assert client.get('/api/auth/session',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
