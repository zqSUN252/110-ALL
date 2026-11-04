"""Synthetic real-PG regression cases for WH/F6/SHIP/RATE/PARTY."""
from decimal import Decimal
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.common import DomainError, Actor
from app.models import User, Invoice
from app.finance import service as finance


def who(db):
    return Actor(db.scalar(select(User.id).where(User.username=='admin')), {'*'}, set())


def call(db,actor,fn,**payload):
    return fn(db,actor,dict(idempotency_key=uuid4().hex,**payload))


def seed(db,actor):
    wh=call(db,actor,finance.create_warehouse,name='Synthetic warehouse')['id']
    party=call(db,actor,finance.create_party,name='Synthetic party',warehouse_id=wh)['id']
    return wh,party


def receipt_payload(wh,party,number=1,**extra):
    return dict(warehouse_id=wh,party_id=party,series='SYN',number=number,received_on='2026-11-03',
                transport='AIR',origin='GZ',goods_class='general',item_zh='合成测试货物',
                specs=[dict(length='100',width='50',height='20',unit='CM',pieces=2,kg='7.5') for _ in range(20)],**extra)


def test_receipt_http_twenty_specs_unique_identity_and_review(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        h={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'receipt-one'}
        result=client.post('/api/receipts',json=receipt_payload(wh,party),headers=h)
        assert result.status_code==200,result.text
        r=result.json();assert r['pieces']==40 and r['kg']=='150.000000' and r['cbm']=='4.000000'
        assert r['declaration_required'] and r['actual_release_at'] is None
        assert client.post('/api/receipts',json=receipt_payload(wh,party),headers=h).json()==r
        h['Idempotency-Key']='duplicate'
        assert client.post('/api/receipts',json=receipt_payload(wh,party),headers=h).status_code==409
        h['Idempotency-Key']='edit'
        changed=client.patch('/api/receipts/'+r['id'],json={'expected_version':1,'item_en':'Manually checked','declaration_required':None},headers=h)
        assert changed.status_code==200,changed.text
        assert not changed.json()['declaration_required']
        h['Idempotency-Key']='stale-review'
        assert client.post('/api/receipts/'+r['id']+'/review',json={'expected_version':1},headers=h).status_code==409


def test_batch_outbound_cannot_overdraw_and_does_not_release(app):
    from app.cargo import service as cargo
    from app.shipments import service as shipping
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        s=call(db,actor,shipping.create_shipment,warehouse_id=wh,reference='SYN-AIR',transport='AIR',origin='GZ')
        b=call(db,actor,cargo.dispatch,shipment_id=s['id'],rows=[{'receipt_id':r['id'],'expected_version':1,'pieces':15,'kg':'50','cbm':'1'}],occurred_on='2026-11-04')
        assert b['rows'][0]['pieces']==15
        current=cargo.get_receipt(db,actor,r['id'])
        assert current['remaining']=={'pieces':25,'kg':'100.000000','cbm':'3.000000'}
        assert current['actual_release_at'] is None
        with pytest.raises(DomainError,match='remaining'):
            call(db,actor,cargo.dispatch,shipment_id=s['id'],rows=[{'receipt_id':r['id'],'expected_version':2,'pieces':26,'kg':'1','cbm':'1'}],occurred_on='2026-11-04')


def test_manual_ngn_pcs_survives_destination_change_and_unknown_price(app):
    from app.pricing import service as pricing
    from app.cargo import service as cargo
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        q=call(db,actor,pricing.quote_receipt,id=r['id'],expected_version=1,manual={'unit_price':'2000','currency':'NGN','unit':'PCS'},fx={})
        assert q['quote']['amount_ngn']=='80000.00'
        changed=call(db,actor,cargo.update_receipt,id=r['id'],expected_version=2,destination='UNKNOWN')
        assert changed['quote']['unit']=='PCS' and changed['quote']['amount_ngn']=='80000.00'
        r2=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,2))
        assert r2['quote'] is None


def test_actual_inbound_analysis_mc_staff_filter_not_receipt_prefix(app):
    from app.cargo import service as cargo
    from app.warehouses import analytics
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
        call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,1,destination='P.H',salesperson='Alice'))
        call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,2,destination='ABA',salesperson='MC recorder'))
        a=analytics.inbound(db,actor,on='2026-11-03',warehouse_id=wh)
        assert a['totals']=={'pieces':80,'kg':'300.000000','cbm':'8.000000'}
        assert len(a['destinations'])==2 and a['salespeople'][0]['name']=='Alice'
        assert len(a['salespeople'])==1
        assert len(analytics.inbound(db,actor,on='2026-11-03',warehouse_id=wh,salesperson='Alice')['rows'])==1


def test_master_edit_preserves_phone_provenance_and_multi_category(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        headers={'X-CSRF-Token':login['csrf_token'],'Idempotency-Key':'party-edit'}
        changed=client.patch('/api/parties/'+party,json={'expected_version':1,'categories':['customer','warehouse','clearing_agent'],'phone':'+234 700 000 0000','phone_source':'Manual synthetic evidence'},headers=headers)
        assert changed.status_code==200,changed.text
        data=changed.json();assert len(data['categories'])==3
        assert data['phones'][0]['raw']=='+234 700 000 0000' and data['phones'][0]['normalized']=='+2347000000000'
        assert data['phones'][0]['actor_id']==actor.id
        headers['Idempotency-Key']='warehouse-edit'
        result=client.patch('/api/warehouses/'+wh,json={'expected_version':1,'address':'Synthetic address','contact':'Synthetic contact','source':'Explicit user supplied'},headers=headers)
        assert result.status_code==200,result.text
        assert client.get('/api/warehouses/'+wh).json()['address']=='Synthetic address'


def test_translation_disabled_and_late_candidate_cannot_replace_manual(app):
    from app.cargo import service as cargo
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor)
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        response=client.post('/api/receipts/'+r['id']+'/translation',json={'expected_version':1},headers={'X-CSRF-Token':login['csrf_token'],'Idempotency-Key':'translation'})
        assert response.status_code==200,response.text
        assert response.json()['status']=='unavailable'


def test_receipt_attachment_export_and_f6_external_does_not_double_stock(app):
    from app.cargo import service as cargo
    from app.packing import imports
    from test_packing import upload
    from app.warehouses import exports
    from app.attachments import read_file
    from app.models import Attachment
    from io import BytesIO
    from openpyxl import load_workbook
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor)
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        preview=call(db,actor,imports.preview_import,warehouse_id=wh,attachment_id=upload(db,actor),kind='f6')
        selection=[{'row_id':row['id'],'native_receipt_id':r['id']} for row in preview['rows'] if row['eligible']]
        call(db,actor,imports.commit_import,id=preview['id'],expected_version=1,selection=selection)
        assert cargo.get_receipt(db,actor,r['id'])['remaining']['pieces']==40
        exported=call(db,actor,exports.export_receipts,warehouse_id=wh,mode='visible',receipt_ids=[r['id']])
        data=read_file(app.state.settings,db.get(Attachment,exported['attachment_id']))
        book=load_workbook(BytesIO(data));assert book.active.max_row==2
        assert book.active.cell(2,1).value=='SYN1' and book.active.cell(2,5).value==40


def test_receipt_merge_preserves_old_specs_and_source_conservation(app):
    from app.cargo import service as cargo
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
        one=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        two=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,2))
        assert hasattr(cargo,'merge_receipts'),'Explicit source merge command is missing'
        merged=call(db,actor,cargo.merge_receipts,id=one['id'],expected_version=1,sources=[{'receipt_id':two['id'],'expected_version':1}],occurred_on='2026-11-04',reason='Confirmed synthetic repacking')
        assert merged['remaining']=={'pieces':80,'kg':'300.000000','cbm':'8.000000'}
        assert cargo.get_receipt(db,actor,two['id'])['remaining']['pieces']==0
        assert len(merged['specs'])==40


def test_permission_editor_rejects_nonexistent_warehouse(app):
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        target=next(u for u in client.get('/api/admin/users').json() if u['username']=='operator')
        response=client.put('/api/admin/users/'+target['id']+'/grants',json={'expected_version':target['version'],'capabilities':['cargo.read'],'warehouse_ids':['missing-warehouse']},headers={'X-CSRF-Token':login['csrf_token'],'Idempotency-Key':'missing-scope'})
        assert response.status_code==422,response.text


def test_concurrent_dispatch_one_winner_no_negative_stock(app):
    from app.cargo import service as cargo
    from app.shipments import service as shipping
    from concurrent.futures import ThreadPoolExecutor
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        s=call(db,actor,shipping.create_shipment,warehouse_id=wh,reference='SYN',transport='AIR')
    def execute(n):
        try:
            with app.state.session_factory() as db,db.begin():
                cargo.dispatch(db,actor,{'idempotency_key':'concurrent'+str(n),'shipment_id':s['id'],'occurred_on':'2026-11-04','rows':[{'receipt_id':r['id'],'expected_version':1,'pieces':30,'kg':'100','cbm':'3'}]})
            return 'ok'
        except DomainError as e:return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(execute,[1,2]))
    assert results.count('ok')==1
    with app.state.session_factory() as db:
        assert cargo.get_receipt(db,actor,r['id'])['remaining']=={'pieces':10,'kg':'50.000000','cbm':'1.000000'}


def test_late_translation_external_transport_only_fake(app,monkeypatch):
    from dataclasses import replace
    from app.cargo import service as cargo,translation
    from app.evidence.recognition import ModelConfig
    import asyncio,httpx
    settings=replace(app.state.settings,model_calls_enabled=True)
    for key,value in {'MULTIMODAL_ENABLED':'true','MULTIMODAL_BASE_URL':'https://synthetic.invalid/v1','MULTIMODAL_API_KEY':'synthetic-not-a-secret','MULTIMODAL_MODEL':'synthetic-model'}.items():monkeypatch.setenv(key,value)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;actor=who(db);wh,party=seed(db,actor);r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        candidate=call(db,actor,translation.request_translation,id=r['id'],expected_version=1)
        assert candidate['status']=='pending'
    def transport(request):
        with app.state.session_factory() as db,db.begin():call(db,actor,cargo.update_receipt,id=r['id'],expected_version=1,item_en='Manual authoritative wording')
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"english":"Candidate wording"}'}}]})
    result=asyncio.run(translation.translate_once(app.state.session_factory,settings,transport=httpx.MockTransport(transport)))
    assert result['status']=='stale'
    with app.state.session_factory() as db,db.begin():
        assert cargo.get_receipt(db,actor,r['id'])['item_en']=='Manual authoritative wording'
        current=translation.get_translation(db,actor,candidate['id'])
        assert current['candidate']=='Candidate wording' and current['source_text']=='合成测试货物'
        with pytest.raises(DomainError,match='no longer matches'):
            call(db,actor,translation.accept_translation,id=current['id'],expected_version=current['version'],receipt_version=2)


def test_effective_price_date_and_clearing_payable_upsert(app):
    from app.pricing import service as pricing
    from app.cargo import service as cargo
    from app.shipments import service as shipping
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
        call(db,actor,pricing.create_card,transport='AIR',origin='GZ',goods_class='general',destination='ABA',unit='KG',currency='USD',unit_price='8',valid_from='2026-10-01',valid_to='2026-10-26',source='Historical synthetic card')
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,1,destination='ABA'))
        assert r['quote'] is None
        call(db,actor,pricing.create_card,transport='AIR',origin='GZ',goods_class='general',destination='ABA',unit='KG',currency='USD',unit_price='9',valid_from='2026-11-01',valid_to='2026-11-30',source='Current synthetic card')
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,2,destination='ABA'))
        assert r['quote']['charge_usd']=='1350.00' and r['quote']['charge_ngn']=='0.00' and r['quote']['amount_ngn'] is None
        s=call(db,actor,shipping.create_shipment,warehouse_id=wh,reference='Clearing synthetic',transport='SEA',clearing_on='2026-11-04',clearing_party_id=party,clearing_amount_ngn='10000')
        changed=call(db,actor,shipping.update_shipment,id=s['id'],expected_version=1,reference='Edited reference')
        assert changed['clearing_invoice_id']==s['clearing_invoice_id'] and len(list(db.scalars(select(Invoice))))==1
        preview=finance.adjustment_preview(db,actor,s['clearing_invoice_id'],'12000')
        call(db,actor,shipping.update_shipment,id=s['id'],expected_version=2,clearing_amount_ngn='12000',finance_preview=preview)
        assert finance.get_invoice(db,actor,s['clearing_invoice_id'])['amount_ngn']=='12000.00'


def test_replay_loses_review_and_financial_permissions(app):
    from app.cargo import service as cargo
    from app.packing import service as packing
    from test_packing import make_document
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        p={'id':r['id'],'expected_version':1,'idempotency_key':'review-replay'}
        cargo.review_receipt(db,actor,p)
        low=Actor(actor.id,{'cargo.manage','cargo.read'},{wh})
        with pytest.raises(DomainError):cargo.review_receipt(db,low,p)
        d,t=make_document(db,actor,wh,party)
        p={'id':t['id'],'expected_version':1,'idempotency_key':'arrival-replay','pieces':3,'kg':'3','cbm':'1','occurred_on':'2026-11-04','source':'Synthetic','mode':'air'}
        packing.arrive(db,actor,p)
        low=Actor(actor.id,{'packing.arrive','finance.read','finance.invoice'},{wh})
        with pytest.raises(DomainError):packing.arrive(db,low,p)


def test_f6_explicit_correction_keeps_raw_and_never_creates_stock(app):
    from app.packing import imports
    from app.models import StockEvent
    from test_packing import upload
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor)
        b=call(db,actor,imports.preview_import,warehouse_id=wh,attachment_id=upload(db,actor),kind='f6')
        eligible=[r for r in b['rows'] if r['eligible']]
        call(db,actor,imports.commit_import,id=b['id'],expected_version=1,selection=[{'row_id':r['id'],'values':{'kg':'11' if i==0 else r['kg']}} for i,r in enumerate(eligible)])
        rows=imports.list_f6(db,actor,warehouse_id=wh)
        corrected=next(r for r in rows if r['id']==eligible[0]['id'])
        assert corrected['kg']=='11.000000' and corrected['raw'][5]==10
        assert not list(db.scalars(select(StockEvent)))


def test_nonempty_logistics_downgrade_refuses_to_erase_source_facts(app):
    from app.cargo import service as cargo
    from app.cli import migration_config
    from alembic import command
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
    config=migration_config();config.attributes['database_url']=app.state.settings.database_url
    with pytest.raises(RuntimeError,match='Preserve logistics'):
        command.downgrade(config,'b11000200002')
    with app.state.session_factory() as db:assert cargo.get_receipt(db,actor,r['id'])['remaining']['pieces']==40


def test_command_replay_rechecks_revoked_domain_read_permission(app):
    from app.cargo import service as cargo
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
        payload=receipt_payload(wh,party)|{'idempotency_key':'read-replay'}
        cargo.create_receipt(db,actor,payload)
        revoked=Actor(actor.id,{'cargo.manage'},{wh})
        with pytest.raises(DomainError):cargo.create_receipt(db,revoked,payload)
