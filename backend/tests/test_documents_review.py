"""Task 4 gate I1-I5: real PG, worker, HTTP and native file regressions."""
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
import re
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select
from app.models import Ticket, Receipt, ExportMember, Grant, WarehouseScope, User
from app.documents import exports, notifications
from app.documents.projection import packing_projection, today
from app.documents.rendering import render_artifact
from app.packing import service as packing
from app.pricing import service as pricing
from app.cargo import service as cargo
from app.finance import service as finance
from test_logistics import who, call, seed, receipt_payload
from test_packing import make_document


def login(client, username='admin'):
    response=client.post('/api/auth/login',json={'username':username,'password':'synthetic-password-123'})
    assert response.status_code==200,response.text
    return {'X-CSRF-Token':response.json()['csrf_token'],'Idempotency-Key':uuid4().hex}


def test_i1_multiticket_worker_preserves_selection_despite_reversed_member_uuid(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor)
        doc,first=make_document(db,actor,wh,party)
        second=call(db,actor,packing.create_ticket,document_id=doc['id'],party_id=party,receipt='SYN-SECOND',transport='AIR',origin='GZ',pieces=2,kg='2',cbm='1')
        queued=call(db,actor,exports.queue_export,ticket_ids=[first['id'],second['id']],format='xlsx',kind='packing')
        for member in db.scalars(select(ExportMember).where(ExportMember.export_id==queued['id'])):
            member.id='ffffffff-ffff-4fff-8fff-ffffffffffff' if member.ticket_id==first['id'] else '00000000-0000-4000-8000-000000000000'
    assert exports.export_once(app.state.settings,app.state.session_factory)
    with TestClient(app) as client:
        login(client)
        status=client.get('/api/exports/'+queued['id']).json()
        assert status['status']=='completed',status
        response=client.get('/api/exports/'+queued['id']+'/file')
        assert response.status_code==200,response.text
        book=load_workbook(BytesIO(response.content),data_only=True)
        assert [row[0] for row in book['Master'].iter_rows(min_row=2,values_only=True)]==['SYN1','SYN-SECOND']
        with app.state.session_factory() as db,db.begin():
            db.info['settings']=app.state.settings
            pending=call(db,who(db),exports.queue_export,ticket_ids=[first['id'],second['id']],format='xlsx',kind='packing')
            call(db,who(db),packing.update_ticket,id=first['id'],expected_version=first['version'],mark='Actual changed source')
        assert client.get('/api/exports/'+queued['id']+'/file').status_code==409
        assert exports.export_once(app.state.settings,app.state.session_factory)
        stale=client.get('/api/exports/'+pending['id']).json()
        assert stale['status']=='failed' and stale['error_code']=='stale_export'


def card(db,actor,price='10',destination='P.H'):
    on=today(db)
    return call(db,actor,pricing.create_card,transport='AIR',origin='GZ',goods_class='general',destination=destination,unit='KG',currency='USD',unit_price=price,valid_from=on.replace(month=1,day=1).isoformat(),valid_to=on.replace(month=12,day=31).isoformat(),source='Synthetic current standard')


@pytest.mark.parametrize('kind',['rates','receipt'])
@pytest.mark.parametrize('change',['price','new','conflict','disable'])
def test_i2_live_price_selection_invalidates_old_image_and_copy(app,kind,change):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor)
        initial=card(db,actor) if change!='new' else None
        receipt=call(db,actor,cargo.create_receipt,**(receipt_payload(wh,party,destination='P.H')|{'received_on':today(db).isoformat()}))
        old_quote=receipt['quote_id']
        old=call(db,actor,notifications.render,kind=kind,receipt_ids=[receipt['id']])
        # A nonselected port's new card must not invalidate the scoped notice.
        card(db,actor,'99','ABA')
    with TestClient(app) as client:
        headers=login(client)
        image='/api/notifications/'+old['id']+'/image'
        assert client.get(image).status_code==200
        with app.state.session_factory() as db,db.begin():
            db.info['settings']=app.state.settings;actor=who(db)
            if change in ('price','disable'):
                call(db,actor,pricing.update_card,id=initial['id'],expected_version=initial['version'],**({'unit_price':'20'} if change=='price' else {'active':False}))
            else:card(db,actor,'20')
            assert db.get(Receipt,receipt['id']).quote_id==old_quote
        image_response=client.get(image)
        copy_response=client.post('/api/notifications/'+old['id']+'/copy-events',json={'expected_version':1,'clipboard_success':True},headers=headers)
        assert [image_response.status_code,copy_response.status_code]==[409,409]
        assert image_response.json()['error']['code']=='stale_generation'
        assert copy_response.json()['error']['code']=='stale_generation'
        with app.state.session_factory() as db,db.begin():
            db.info['settings']=app.state.settings
            fresh=call(db,who(db),notifications.render,kind=kind,receipt_ids=[receipt['id']])
        assert ('No applicable effective price card' in fresh['text']) if change=='disable' else ('USD 20.00 PER KG' in fresh['text'])
        assert client.get('/api/notifications/'+fresh['id']+'/image').status_code==200
        headers['Idempotency-Key']=uuid4().hex
        assert client.post('/api/notifications/'+fresh['id']+'/copy-events',json={'expected_version':1,'clipboard_success':True},headers=headers).status_code==200


def test_i3_native_receipt_mark_refresh_cancel_and_warehouse_scope(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);other,_=seed(db,actor)
        receipt=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        generation=call(db,actor,notifications.render,kind='receipt',receipt_ids=[receipt['id']])
        copied=call(db,actor,notifications.copy_success,id=generation['id'],expected_version=1,clipboard_success=True)
        uid=db.scalar(select(User.id).where(User.username=='operator'))
        db.add_all([Grant(user_id=uid,capability='documents.read'),Grant(user_id=uid,capability='documents.notify'),WarehouseScope(user_id=uid,warehouse_id=other)])
    path='/api/receipts/'+receipt['id']+'/notification-marks'
    with TestClient(app) as client:
        headers=login(client)
        response=client.get(path)
        assert response.status_code==200,response.text
        assert len(response.json())==1
        mark=response.json()[0]
        assert mark['copy_id']==copied['id'] and mark['version']==1
        assert mark['actor_id']==actor.id and mark['copied_at'] and mark['kind']=='receipt'
        response=client.post('/api/notification-copies/'+mark['copy_id']+'/cancel',json={'expected_version':mark['version'],'reason':'Synthetic correction'},headers=headers)
        assert response.status_code==200,response.text
    with TestClient(app) as fresh_client:
        login(fresh_client)
        assert fresh_client.get(path).json()==[]
        login(fresh_client,'operator')
        assert fresh_client.get(path).status_code==403


@pytest.mark.parametrize('clearing',['0','900'])
def test_i4_xlsx_currency_totals_preserve_true_charge_components_on_all_sheets(app,clearing):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor)
        doc,ticket=make_document(db,actor,wh,party,'SEA' if clearing!='0' else 'AIR')
        lines=[{'component':'freight','currency':'NGN','unit':'PCS','amount':'6000','source':'Synthetic actual freight'}]
        if clearing!='0':lines.append({'component':'clearing','currency':'NGN','unit':'CBM','amount':clearing,'source':'Synthetic actual clearing'})
        ticket=call(db,actor,packing.update_ticket,id=ticket['id'],expected_version=ticket['version'],destination='P.H',charge_components=lines)
        data=packing_projection(db,[db.get(Ticket,ticket['id'])])
        content,_=render_artifact(app.state.settings,data,'xlsx','packing')
    book=load_workbook(BytesIO(content),data_only=False)
    for sheet in (book['Master'],book['1 PORT HARCOURT']):
        columns={cell.value:cell.column for cell in sheet[1]}
        assert 'Clearing NGN' not in columns
        assert sheet.cell(2,columns['NGN charges']).value==6000+int(clearing)
        component_text=sheet.cell(2,columns['Actual Unit Charges']).value
        assert 'freight NGN 2,000/PCS / Total NGN 6000.00' in component_text
        if clearing!='0':assert 'clearing NGN 900/CBM' in component_text
        else:assert 'clearing' not in component_text
    customer='\n'.join(str(row[0]) for row in book['Customer 1'].iter_rows(values_only=True) if row[0])
    assert 'Freight: NGN 2,000 PER PCS / Total NGN 6,000.00' in customer
    if clearing!='0':assert 'Clearing: NGN 900 PER CBM / Total NGN 900.00' in customer
    else:assert 'Clearing:' not in customer
    assert f'Total cost: USD 0.00 + NGN {6000+int(clearing):,.2f}' in customer
    totals={row[0]:row[1] for row in book['Totals'].iter_rows(min_row=2,values_only=True)}
    assert totals['NGN charges']==6000+int(clearing) and totals['USD charges']==0


@pytest.mark.parametrize('kind',['arrival','remove_hold'])
@pytest.mark.parametrize('case',['half_cent','existing_adjustment'])
def test_i5_sea_notification_conversion_matches_shared_invoice_with_rounding(app,case,kind):
    from app.tracking import service as tracking
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor)
        doc,ticket=make_document(db,actor,wh,party,'SEA')
        ticket=call(db,actor,packing.update_ticket,id=ticket['id'],expected_version=ticket['version'],freight_usd='0.03' if case=='half_cent' else '1',ngn_per_usd='1.5',destination_warehouse_id=wh,**({'contact_raw':'ON HOLD BY SUPPLIER'} if kind=='remove_hold' else {}))
        batches=[(3,'3','1')] if case=='half_cent' else [(1,'1','0.333333'),(1,'1','0.333333')]
        for pcs,kg,cbm in batches:
            ticket=call(db,actor,packing.arrive,id=ticket['id'],expected_version=ticket['version'],pieces=pcs,kg=kg,cbm=cbm,occurred_on='2026-11-04',source='Synthetic actual sea batch',mode='sea')
        call(db,actor,tracking.set_node,target_type='ticket',target_id=ticket['id'],expected_version=ticket['version'],node='warehouse_arrived',mode='manual',destination_warehouse_id=wh,occurred_at='2026-11-04T10:00:00+08:00',source='Actual warehouse confirmation')
        if kind=='remove_hold':
            from app.documents import release
            from app.models import HoldFact
            hold=db.scalar(select(HoldFact).where(HoldFact.ticket_id==ticket['id']))
            call(db,actor,release.remove_holds,members=[{'hold_id':hold.id,'expected_version':hold.version,'source_version':db.get(Ticket,ticket['id']).version}],reason='Supplier confirmed',removed_on='2026-11-04',confirmed_phone='+2347000000000',ngn_per_usd='1.5')
        notice=call(db,actor,notifications.render,kind=kind,ticket_ids=[ticket['id']])
        invoice=finance.get_invoice(db,actor,ticket['invoice_id'])
        converted=Decimal(re.search(r'= ₦([\d.]+)',notice['text']).group(1))
        assert converted==finance.settle(invoice['charge_usd'],'USD',{'direction':'NGN_PER_USD','rate':'1.5'})
        adjustment=Decimal(invoice['rounding_adjustment_ngn'])
        assert converted+Decimal(invoice['charge_ngn'])+adjustment==Decimal(invoice['amount_ngn'])
        assert (f"Total: *₦{invoice['amount_ngn']}*" if kind=='arrival' else f"Total Payable: NGN {invoice['amount_ngn']}") in notice['text']
        if case=='existing_adjustment':
            assert adjustment!=0
            assert f'Rounding adjustment (NGN): {adjustment}' in notice['text']
        else:assert converted==Decimal('0.05') and adjustment==0
