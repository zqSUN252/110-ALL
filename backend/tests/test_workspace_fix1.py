"""Real-PG review regressions: selection capacity, grant versions and copy projection."""
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.models import Payment,User,Grant,WarehouseScope,BankEntry,BankClaim,EvidenceCase
from app.finance import service as finance
from test_logistics import who,call,seed
from test_workspace import login


def test_option_pages_search_later_payment_and_claim_without_cross_scope(app):
    # Explicit review reproduction: use the committed original endpoint on this
    # isolated test app, without reverting the running workspace or database.
    import os
    if os.environ.get('MCSTAR_FIX_BASE_OPTIONS')=='1':
        import subprocess
        original=subprocess.check_output(['git','show','d26a407b2c2c9cbe140174e81bd67aa7ef216ccd:backend/app/api/workspace.py'],text=True,encoding='utf-8')
        namespace={'__name__':'fix_base_workspace'};exec(compile(original,'d26a407:workspace.py','exec'),namespace)
        endpoint=namespace['options']
        for route in app.routes:
            if getattr(route,'path',None)=='/api/workspace/options':
                route.dependant.call=lambda **kwargs:endpoint(**{k:v for k,v in kwargs.items() if k!='after'})
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);other,_=seed(db,actor)
        from app.bank.service import create_account
        account=call(db,actor,create_account,warehouse_id=wh,bank_name='Synthetic',account_number='FIX-OPTION-ACCOUNT',currency='NGN')
        payment_ids=[];claim_ids=[]
        for n in range(55):
            payment=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='10',currency='NGN',paid_on='2026-11-01');payment_ids.append(payment['id'])
            entry=BankEntry(account_id=account['id'],currency='NGN',credit='10',debit='0',transaction_date=date(2026,11,1),reference='SYN-SELECT-'+str(n),description='Synthetic bank selector',fingerprint=str(n).zfill(64),issues=[]);db.add(entry);db.flush()
            case=EvidenceCase(warehouse_id=wh,account_id=account['id'],business_reference='SYN-CASE-'+str(n));db.add(case);db.flush()
            claim=BankClaim(entry_id=entry.id,case_id=case.id,state='verified_unallocated',reason='Synthetic selection fixture',actor_id=actor.id);db.add(claim);db.flush();claim_ids.append(claim.id)
        operator=db.scalar(select(User).where(User.username=='operator'))
        db.add_all([Grant(user_id=operator.id,capability=cap) for cap in ['finance.read','bank.read','evidence.read']]);db.add(WarehouseScope(user_id=operator.id,warehouse_id=wh))
        shared=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='10',currency='NGN',paid_on='2026-11-01')
        db.add(EvidenceCase(warehouse_id=other,payment_id=shared['id']))
        blocked_entry=BankEntry(account_id=account['id'],currency='NGN',credit='10',debit='0',reference='PRIVATE-CROSS-PAYMENT',description='Synthetic inaccessible linked payment',fingerprint='e'*64,issues=[]);db.add(blocked_entry);db.flush()
        blocked_case=EvidenceCase(warehouse_id=wh,account_id=account['id']);db.add(blocked_case);db.flush()
        blocked_claim=BankClaim(entry_id=blocked_entry.id,case_id=blocked_case.id,payment_id=shared['id'],state='verified_unallocated',reason='Synthetic cross-scope linked payment',actor_id=actor.id);db.add(blocked_claim);db.flush();blocked_id=blocked_claim.id
        # Same bank account, inaccessible evidence warehouse: no label or ID may escape.
        entry=BankEntry(account_id=account['id'],currency='NGN',credit='10',debit='0',reference='PRIVATE-OTHER-WAREHOUSE',description='Unshared synthetic',fingerprint='f'*64,issues=[]);db.add(entry);db.flush()
        case=EvidenceCase(warehouse_id=other,account_id=account['id']);db.add(case);db.flush()
        hidden=BankClaim(entry_id=entry.id,case_id=case.id,state='verified_unallocated',reason='Synthetic inaccessible evidence',actor_id=actor.id);db.add(hidden);db.flush();hidden_id=hidden.id
    with TestClient(app) as client:
        login(client,'operator')
        for kind,identities in [('payments',payment_ids),('claims',claim_ids)]:
            first=client.get('/api/workspace/options',params={'kind':kind}).json()
            assert len(first['items'])==50 and first.get('next'), 'A visible page must have a continuation'
            assert first['next'] in identities, 'Continuation must never expose a rejected source ID'
            later=client.get('/api/workspace/options',params={'kind':kind,'after':first['next']}).json()
            assert set(r['id'] for r in first['items']+later['items'])==set(identities)
            chosen=sorted(identities)[-1]
            found=client.get('/api/workspace/options',params={'kind':kind,'query':chosen}).json()
            assert [r['id'] for r in found['items']]==[chosen]
            assert found['items'][0]['label']!=chosen
        assert client.get('/api/workspace/options',params={'kind':'claims','query':hidden_id}).json()['items']==[]
        assert client.get('/api/workspace/options',params={'kind':'claims','query':blocked_id}).json()=={'items':[],'next':None}
        assert client.get('/api/workspace/options',params={'kind':'payments','query':shared['id']}).json()=={'items':[],'next':None}
        assert client.get('/api/workspace/bank-claim/'+hidden_id).status_code==403


def test_grants_projection_includes_current_user_version_and_existing_sets(app):
    with TestClient(app) as client:
        login(client)
        with app.state.session_factory() as db:
            user=db.scalar(select(User).where(User.username=='operator'));identity=user.id;version=user.version
        result=client.get('/api/admin/users/'+identity+'/grants').json()
        assert result['version']==version
        assert set(result['capabilities'])=={'attachments.read','attachments.write'}
        assert result['warehouse_ids']==[]
        assert client.get('/api/admin/users/missing/grants').status_code==404


def test_loading_marks_project_current_copy_and_do_not_pollute_source_fingerprint(app):
    from test_documents import prepared
    from app.models import Shipment
    from app.documents import notifications
    from app.packing import service as packing
    from app.tracking import service as tracking
    with app.state.session_factory() as db,db.begin():
        actor,wh,document,ticket=prepared(db,app)
        call(db,actor,tracking.set_node,target_type='shipment',target_id=document['shipment_id'],expected_version=db.get(Shipment,document['shipment_id']).version,node='loading',mode='manual',occurred_at='2026-11-01T08:00:00+08:00',source='Synthetic loading')
        notice=call(db,actor,notifications.render,kind='loading',entry='packing-row',ticket_ids=[ticket['id']])
        copied=call(db,actor,notifications.copy_success,id=notice['id'],expected_version=notice['version'],clipboard_success=True)
        current=packing.get_ticket(db,actor,ticket['id'])
        assert current.get('loading_notified') is True
        assert current['notification_marks'][0]['copy_id']==copied['id']
        # A display mark is not a business-source change; repeated authorized copy remains valid.
        notifications.current_generation(db,actor,db.get(__import__('app.models',fromlist=['Notification']).Notification,notice['id']))
        cancelled=call(db,actor,notifications.cancel_copy,id=copied['id'],expected_version=copied['version'],reason='Synthetic wrong copy selection')
        assert cancelled['ticket_ids']==[ticket['id']]
        assert packing.get_ticket(db,actor,ticket['id'])['loading_notified'] is False


def test_destination_only_photo_upload_and_pickup_are_purpose_scoped(app):
    from test_documents import prepared
    from app.documents import release
    from app.packing import service as packing
    from app.tracking import service as tracking
    from sqlalchemy import delete
    from PIL import Image
    from io import BytesIO
    from uuid import uuid4
    with app.state.session_factory() as db,db.begin():
        actor,origin,doc,t=prepared(db,app)
        dest,_=seed(db,actor)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],destination_warehouse_id=dest)
        call(db,actor,tracking.set_node,target_type='ticket',target_id=t['id'],expected_version=t['version'],node='warehouse_arrived',mode='manual',destination_warehouse_id=dest,occurred_at='2026-11-03T10:00:00+08:00',source='Synthetic destination receipt')
        t=packing.get_ticket(db,actor,t['id'])
        order=call(db,actor,release.issue_order,members=[{'ticket_id':t['id'],'expected_version':t['version']}],mode='credit',promise_on='2026-12-01',guarantor='Synthetic guarantor')
        operator=db.scalar(select(User).where(User.username=='operator'))
        db.execute(delete(Grant).where(Grant.user_id==operator.id))
        db.add_all([Grant(user_id=operator.id,capability=c) for c in ['release.read','release.physical']]);db.add(WarehouseScope(user_id=operator.id,warehouse_id=dest))
        other=db.scalar(select(User).where(User.username=='other'))
        db.execute(delete(Grant).where(Grant.user_id==other.id));db.add_all([Grant(user_id=other.id,capability=c) for c in ['release.read','release.physical']]);db.add(WarehouseScope(user_id=other.id,warehouse_id=origin))
    image=BytesIO();Image.new('RGB',(8,8),'white').save(image,format='PNG')
    with TestClient(app) as client:
        csrf=login(client,'operator')
        def headers():return {'X-CSRF-Token':csrf,'Idempotency-Key':uuid4().hex}
        url='/api/release-orders/'+order['id']+'/photos'
        def upload(version=order['version'],content=image.getvalue(),ticket=t['id']):return client.post(url,data={'expected_version':version,'ticket_id':ticket},files={'file':('pickup.png',content,'image/png')},headers=headers())
        assert client.get('/api/workspace/options?kind=tickets').status_code==403
        assert client.get('/api/tickets/'+t['id']).status_code==403
        photo=upload();assert photo.status_code==201,photo.text
        assert upload(version=0).status_code==409
        assert upload(content=b'not an image').status_code==400
        assert upload(content=b'x'*(app.state.settings.max_attachment_bytes+1)).status_code==413
        assert upload(ticket=str(uuid4())).status_code in (403,404)
        assert client.post(url,data={'expected_version':order['version'],'ticket_id':t['id'],'owner_id':'arbitrary'},files={'file':('pickup.png',image.getvalue(),'image/png')},headers=headers()).status_code==422
        assert client.get(url+'/'+photo.json()['id'],params={'expected_version':order['version'],'ticket_id':t['id']}).status_code==200
        with TestClient(app) as denied:
            other_csrf=login(denied,'other')
            assert denied.post(url,data={'expected_version':order['version'],'ticket_id':t['id']},files={'file':('pickup.png',image.getvalue(),'image/png')},headers={'X-CSRF-Token':other_csrf,'Idempotency-Key':uuid4().hex}).status_code==403
            assert denied.get(url+'/'+photo.json()['id'],params={'expected_version':order['version'],'ticket_id':t['id']}).status_code==403
        picked=client.post('/api/physical-releases',json={'order_id':order['id'],'expected_version':order['version'],'ticket_id':t['id'],'pieces':3,'kg':'3','cbm':'1','occurred_at':'2026-11-04T10:00:00+08:00','photo_ids':[photo.json()['id']]},headers=headers())
        assert picked.status_code==200,picked.text
        assert client.get('/api/physical-releases/'+picked.json()['id']+'/photos/'+photo.json()['id']).status_code==200
        current=client.get('/api/release-orders/'+order['id']+'/eligibility').json()
        assert current['physical_releases'][0]['photo_ids']==[photo.json()['id']]
        assert 'invoice' not in current and 'amount_ngn' not in str(current)
        assert upload(version=picked.json()['order_version']).status_code==409
        assert client.get('/api/physical-releases/'+picked.json()['id']+'/photos/'+str(uuid4())).status_code==404
