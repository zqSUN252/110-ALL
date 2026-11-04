from fastapi.testclient import TestClient
from test_documents import prepared
from test_logistics import call
from app.models import Shipment


def test_public_wrong_suffix_and_unknown_are_indistinguishable(app):
    with app.state.session_factory() as db, db.begin():
        actor,wh,doc,t=prepared(db,app)
    with TestClient(app) as client:
        bad=client.post('/api/tracking/query',json={'reference':'SYN1','last4':'1234'})
        missing=client.post('/api/tracking/query',json={'reference':'missing','last4':'1234'})
        assert bad.status_code==missing.status_code==404
        assert bad.json()==missing.json()
        good=client.post('/api/tracking/query',json={'reference':'SYN1','last4':'0000'})
        assert good.status_code==200
        assert 'Synthetic goods' not in good.text and '2347000000000' not in good.text
        assert 'amount' not in good.text and 'item_en' not in good.text


def test_manual_node_is_independent_and_never_physical(app):
    from app.tracking import service as tracking
    from app.documents import release
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        main=call(db,actor,tracking.set_node,target_type='shipment',target_id=doc['shipment_id'],expected_version=db.get(Shipment,doc['shipment_id']).version,node='departed',mode='manual',occurred_at='2026-11-01T08:00:00+08:00',source='Synthetic sailing')
        call(db,actor,tracking.set_node,target_type='ticket',target_id=t['id'],expected_version=t['version'],node='departed',mode='manual',occurred_at='2026-11-02T08:00:00+08:00',source='Actual exception')
        call(db,actor,tracking.set_node,target_type='shipment',target_id=doc['shipment_id'],expected_version=main['target_version'],node='departed',mode='manual',occurred_at='2026-11-03T08:00:00+08:00',source='Main correction')
        assert tracking.timeline(db,t['id'])['nodes']['departed']['occurred_at'].startswith('2026-11-02')
        assert release.physical_totals(db,t['id'])['pieces']==0


def test_duplicate_receipt_and_courier_prove_every_source(app):
    from test_logistics import who,seed,receipt_payload
    from test_packing import make_document
    from app.cargo import service as cargo
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        other_doc,other=make_document(db,actor,wh,t['party_id'])
        call(db,actor,packing.update_ticket,id=other['id'],expected_version=other['version'],phone_normalized='+2347000009999',phone_confirmed=True,contact_raw='+2347000009999')
        native=call(db,actor,cargo.create_receipt,**receipt_payload(wh,t['party_id'],customer_phone='+2347000000000',courier_number='COURIER-SHARED'))
        call(db,actor,cargo.create_receipt,**receipt_payload(wh,t['party_id'],2,customer_phone='+2347000009999',courier_number='COURIER-SHARED'))
    with TestClient(app) as client:
        receipt=client.post('/api/tracking/query',json={'reference':'SYN1','last4':'0000'}).json()['rows']
        assert all(row['id']!=other['id'] for row in receipt)
        courier=client.post('/api/tracking/query',json={'reference':'COURIER-SHARED','last4':'0000'}).json()['rows']
        assert {row['id'] for row in courier}=={native['id']}
        for _ in range(30):last=client.post('/api/tracking/query',json={'reference':'missing','last4':'1234'})
        assert last.status_code==429


def test_actual_provider_transport_verification_never_returns_code(app):
    from dataclasses import replace
    import httpx
    captured={}
    def transport(request):
        import json
        captured.update(json.loads(request.content));return httpx.Response(200,json={'delivery_reference':'synthetic-delivery'})
    app.state.settings=replace(app.state.settings,sending_enabled=True,phone_delivery_enabled=True,phone_provider_url='https://synthetic.invalid/verify',phone_provider_token='synthetic-secret')
    app.state.phone_transport=httpx.MockTransport(transport)
    with TestClient(app) as client:
        requested=client.post('/api/customer/verification',json={'phone':'+2347000000000'})
        assert requested.status_code==200 and captured['code'] not in requested.text
        identity=requested.json()['challenge_id']
        bad=client.post('/api/customer/register',json={'challenge_id':identity,'code':'wrong'})
        assert bad.status_code==400
        good=client.post('/api/customer/register',json={'challenge_id':identity,'code':captured['code']})
        assert good.status_code==200 and client.get('/api/customer/documents').status_code==200
        assert client.post('/api/customer/register',json={'challenge_id':identity,'code':captured['code']}).status_code==400
        assert client.post('/api/customer/logout',headers={'X-CSRF-Token':good.json()['csrf_token']}).status_code==204
        assert client.get('/api/customer/documents').status_code==401


def test_warehouse_confirmation_scope_follow_and_covered_arrival_batches(app):
    import pytest
    from app.common import Actor,DomainError
    from app.models import Ticket,User
    from sqlalchemy import select
    from app.tracking import service as tracking
    from app.documents import notifications,release
    from app.packing import service as packing
    from test_logistics import who,seed
    from test_packing import make_document
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;admin=who(db);origin,party=seed(db,admin);dest,_=seed(db,admin);wrong,_=seed(db,admin)
        doc,t=make_document(db,admin,origin,party)
        t=call(db,admin,packing.update_ticket,id=t['id'],expected_version=t['version'],destination_warehouse_id=dest,contact_raw='ON HOLD',mark='held source')
        t=call(db,admin,packing.arrive,id=t['id'],expected_version=t['version'],pieces=1,kg='1',cbm='0.333333',occurred_on='2026-11-01',source='Port batch one',mode='air')
        operator=Actor(db.scalar(select(User.id).where(User.username=='operator')),{'tracking.read','tracking.manage','release.read','release.physical'},{dest})
        preview=tracking.warehouse_preview(db,operator,'shipment',doc['shipment_id'],dest)
        assert 'amount' not in str(preview) and 'invoice' not in str(preview)
        call(db,operator,tracking.set_node,target_type='shipment',target_id=doc['shipment_id'],expected_version=preview['expected_version'],node='warehouse_arrived',mode='manual',destination_warehouse_id=dest,occurred_at='2026-11-02T10:00:00+08:00',source='Actual first batch received at destination')
        assert sum(a.pieces for a in tracking.warehouse_arrivals(db,db.get(Ticket,t['id'])))==1
        # Independent warehouse B confirmation on the same shipment retains A's fact.
        other=call(db,admin,packing.create_ticket,document_id=doc['id'],party_id=party,receipt='SYN-B',mark='Different customer B',pieces=2,kg='2',cbm='1',transport='AIR',destination_warehouse_id=wrong)
        other=call(db,admin,packing.arrive,id=other['id'],expected_version=other['version'],pieces=2,kg='2',cbm='1',occurred_on='2026-11-01',source='Separate warehouse B batch',mode='air')
        second=tracking.warehouse_preview(db,admin,'shipment',doc['shipment_id'],wrong)
        call(db,admin,tracking.set_node,target_type='shipment',target_id=doc['shipment_id'],expected_version=second['expected_version'],node='warehouse_arrived',mode='manual',destination_warehouse_id=wrong,occurred_at='2026-11-02T12:00:00+08:00',source='Actual warehouse B receipt')
        assert sum(a.pieces for a in tracking.warehouse_arrivals(db,db.get(Ticket,t['id'])))==1
        assert sum(a.pieces for a in tracking.warehouse_arrivals(db,db.get(Ticket,other['id'])))==2
        changes={'freight_usd':'2'};impact=packing.ticket_change_preview(db,admin,t['id'],changes)
        t=call(db,admin,packing.update_ticket,id=t['id'],expected_version=db.get(Ticket,t['id']).version,freight_usd='2',finance_preview=impact['finance_preview'],reason='Actual price correction only')
        assert sum(a.pieces for a in tracking.warehouse_arrivals(db,db.get(Ticket,t['id'])))==1
        with pytest.raises(DomainError,match='customs'):
            call(db,admin,notifications.render,kind='hold',ticket_ids=[t['id']])
        shipment=db.get(Shipment,doc['shipment_id'])
        call(db,admin,tracking.set_node,target_type='shipment',target_id=shipment.id,expected_version=shipment.version,node='customs_cleared',mode='manual',occurred_at='2026-11-01T12:00:00+08:00',source='Actual customs confirmation')
        assert '已完成清关' in call(db,admin,notifications.render,kind='hold',ticket_ids=[t['id']])['text']
        t=call(db,admin,packing.arrive,id=t['id'],expected_version=db.get(Ticket,t['id']).version,pieces=2,kg='2',cbm='0.666667',occurred_on='2026-11-03',source='Second port batch, not yet warehouse',mode='air')
        assert sum(a.pieces for a in tracking.warehouse_arrivals(db,db.get(Ticket,t['id'])))==1
        call(db,operator,tracking.set_node,target_type='ticket',target_id=t['id'],expected_version=t['version'],node='warehouse_arrived',mode='manual',destination_warehouse_id=dest,occurred_at=None,source='Individual warehouse confirmation cleared')
        assert tracking.warehouse_arrivals(db,db.get(Ticket,t['id']))==[]
        call(db,operator,tracking.set_node,target_type='ticket',target_id=t['id'],expected_version=db.get(Ticket,t['id']).version,node='warehouse_arrived',mode='follow',destination_warehouse_id=dest,source='Explicit restore following')
        assert sum(a.pieces for a in tracking.warehouse_arrivals(db,db.get(Ticket,t['id'])))==1
        with pytest.raises(DomainError):tracking.warehouse_preview(db,operator,'shipment',doc['shipment_id'],wrong)
        call(db,admin,packing.update_ticket,id=t['id'],expected_version=db.get(Ticket,t['id']).version,destination_warehouse_id=wrong)
        assert tracking.warehouse_arrivals(db,db.get(Ticket,t['id']))==[]
