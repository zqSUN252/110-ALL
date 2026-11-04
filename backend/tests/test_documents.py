from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from app.common import Actor, DomainError
from app.models import Ticket, User, Shipment
from test_logistics import who, call, seed
from test_packing import make_document


def prepared(db, app, hold=False):
    from app.packing import service as packing
    from app.tracking import service as tracking
    db.info['settings'] = app.state.settings
    actor = who(db); wh, party = seed(db, actor)
    doc, ticket = make_document(db, actor, wh, party)
    ticket = call(db, actor, packing.update_ticket, id=ticket['id'], expected_version=1,
        contact_raw='ON HOLD BY supplier' if hold else '+2347000000000',
        phone_normalized=None if hold else '+2347000000000', phone_confirmed=not hold,
        destination_warehouse_id=wh, address='Synthetic address', mark='SYN mark', item_en='Synthetic goods')
    ticket = call(db, actor, packing.arrive, id=ticket['id'], expected_version=ticket['version'],
        pieces=3, kg='3', cbm='1', occurred_on='2026-11-01', source='Synthetic arrival', mode='air')
    call(db,actor,tracking.set_node,target_type='ticket',target_id=ticket['id'],expected_version=ticket['version'],node='warehouse_arrived',mode='manual',destination_warehouse_id=wh,occurred_at='2026-11-02T10:00:00+08:00',source='Actual synthetic destination receipt')
    ticket=packing.get_ticket(db,actor,ticket['id'])
    return actor, wh, doc, ticket


def test_phone_provider_is_disabled_without_configuration(app):
    with TestClient(app) as client:
        response = client.post('/api/customer/verification', json={'phone': '+2347000000000'})
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'provider_disabled'
        assert 'otp' not in response.text.lower()


def test_phone_edit_cannot_remove_manufacturer_hold(app):
    from app.documents import release
    from app.packing import service as packing
    with app.state.session_factory() as db, db.begin():
        actor, wh, doc, t = prepared(db, app, hold=True)
        assert release.hold_state(db, 'ticket', t['id'])['active']
        t = call(db, actor, packing.update_ticket, id=t['id'], expected_version=t['version'], contact_raw='', phone_normalized='+2347000000000', phone_confirmed=True)
        assert release.hold_state(db, 'ticket', t['id'])['active']
        with pytest.raises(DomainError, match='manufacturer'):
            call(db, actor, release.issue_order, members=[{'ticket_id':t['id'],'expected_version':t['version']}], mode='credit', promise_on='2026-12-01', guarantor='Synthetic guarantor')


def test_credit_physical_release_and_metadata_invalidation(app):
    from app.documents import release
    from app.packing import service as packing
    from app.attachments import put_file
    from PIL import Image
    from io import BytesIO
    with app.state.session_factory() as db, db.begin():
        actor, wh, doc, t = prepared(db, app)
        order = call(db, actor, release.issue_order, members=[{'ticket_id':t['id'],'expected_version':t['version']}], mode='credit', promise_on='2026-12-01', guarantor='Synthetic guarantor')
        uid=db.scalar(select(User.id).where(User.username=='operator'))
        warehouse=Actor(uid,{'release.physical','release.read'}, {wh})
        assert release.eligible_order(db,warehouse,order['id'])['eligible']
        image=BytesIO();Image.new('RGB',(10,10),'white').save(image,format='PNG')
        photo=put_file(db,Actor(uid,{'attachments.write'},set()),image.getvalue(),'photo.png','image/png','user',uid)
        picked=call(db,warehouse,release.physical_release,order_id=order['id'],expected_version=1,ticket_id=t['id'],pieces=1,kg='1',cbm='0.333333',occurred_at='2026-11-04T10:00:00+08:00',photo_ids=[photo.id])
        assert picked['pieces']==1
        from app.tracking.service import timeline
        assert timeline(db,t['id'])['physical_releases'][0]['photo_ids']==[photo.id]
        changed=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],mark='Edited mark')
        assert not release.eligible_order(db,warehouse,order['id'])['eligible']
        assert release.physical_totals(db,t['id'])['pieces']==1


def test_loading_copy_snapshot_survives_regrouping(app):
    from app.documents import notifications
    from app.tracking import service as tracking
    from app.packing import service as packing
    with app.state.session_factory() as db, db.begin():
        actor,wh,doc,t=prepared(db,app)
        call(db,actor,tracking.set_node,target_type='shipment',target_id=doc['shipment_id'],expected_version=db.get(Shipment,doc['shipment_id']).version,node='loading',mode='manual',occurred_at='2026-10-30T00:30:00+08:00',source='Synthetic loading')
        g=call(db,actor,notifications.render,kind='loading',entry='packing-row',ticket_ids=[t['id']])
        assert '2026-10-30' in g['text']
        with pytest.raises(DomainError):
            call(db,actor,notifications.copy_success,id=g['id'],expected_version=1,clipboard_success=False)
        copied=call(db,actor,notifications.copy_success,id=g['id'],expected_version=1,clipboard_success=True)
        current=packing.get_document(db,actor,doc['id'])
        call(db,actor,packing.split_document,id=doc['id'],expected_version=current['version'],ticket_ids=[t['id']],reference='Synthetic moved')
        cancelled=call(db,actor,notifications.cancel_copy,id=copied['id'],expected_version=1,reason='Wrong copy')
        assert cancelled['ticket_ids']==[t['id']]
        assert notifications.marks(db,[t['id']])[t['id']]==[]


def test_exports_are_real_files_unknown_totals_and_label_size(app):
    from app.documents import exports
    from app.documents.rendering import render_artifact
    from app.documents.projection import packing_projection
    from PIL import Image
    from pypdf import PdfReader
    from openpyxl import load_workbook
    from io import BytesIO
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        projection=packing_projection(db,[db.get(Ticket,t['id'])])
        for fmt in ('xlsx','pdf','png'):
            blob,media=render_artifact(app.state.settings,projection,fmt,'packing')
            if fmt=='xlsx':
                book=load_workbook(BytesIO(blob),data_only=True)
                assert len(book.sheetnames)>=3 and book['Master'].freeze_panes=='E2'
                assert any(1500 in row for row in book['Master'].iter_rows(values_only=True))
            elif fmt=='pdf':assert '1500' in ''.join(p.extract_text() for p in PdfReader(BytesIO(blob)).pages).replace(',','')
            else:
                with Image.open(BytesIO(blob)) as im:assert im.width>500
        blob,_=render_artifact(app.state.settings,projection,'pdf','label_plain')
        page=PdfReader(BytesIO(blob)).pages[0]
        assert abs(float(page.mediabox.width)*25.4/72-76)<.01
        assert abs(float(page.mediabox.height)*25.4/72-130)<.01
        unknown=db.get(Ticket,t['id']);unknown.kg=None
        projection=packing_projection(db,[unknown])
        assert projection['totals']['kg']['value'] is None and projection['totals']['kg']['pending_count']==1


def test_export_worker_rechecks_current_owner_at_bytes_http(app):
    from app.documents import exports
    from app.models import CustomerIdentity,CustomerSession,utcnow
    from app.api.dependencies import token_hash
    from datetime import timedelta
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        uid=db.scalar(select(User.id).where(User.username=='other'))
        customer=CustomerIdentity(user_id=uid,phone='+2347000000000',verified_at=utcnow());db.add(customer);db.flush()
        db.add(CustomerSession(customer_id=customer.id,token_hash=token_hash('synthetic-customer-token'),csrf_hash=token_hash('synthetic-csrf'),expires_at=utcnow()+timedelta(hours=1)))
        queued=exports.queue_customer(db,customer,{'ticket_ids':[t['id']],'format':'xlsx','kind':'packing','idempotency_key':'owner-export'})
    assert exports.export_once(app.state.settings,app.state.session_factory)
    with TestClient(app) as client:
        client.cookies.set('mcstar_customer','synthetic-customer-token')
        response=client.get('/api/customer/exports/'+queued['id']+'/file')
        assert response.status_code==200 and response.content.startswith(b'PK')
        with app.state.session_factory() as db,db.begin():
            obj=db.get(Ticket,t['id']);obj.phone_normalized='+2347000009999';obj.contact_raw=obj.phone_normalized;obj.version+=1
        assert client.get('/api/customer/exports/'+queued['id']+'/file').status_code==404


def test_mbl_requires_real_template_and_preserves_literal_fields(app):
    from app.documents import mbl
    from app.attachments import put_file
    from io import BytesIO
    from openpyxl import Workbook,load_workbook
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        record=call(db,actor,mbl.save_mbl,shipment_id=doc['shipment_id'],expected_version=0,shipper='=1+1',consignee='A & B <cargo>')
        with pytest.raises(DomainError,match='template'):
            mbl.generate(db,actor,record['id'],'BO')
        book=Workbook();book.active['B1']='=SUM(1,2)';b=BytesIO();book.save(b)
        a=put_file(db,actor,b.getvalue(),'actual-synthetic-template.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','user',actor.id)
        call(db,actor,mbl.configure_template,kind='BO',expected_version=0,attachment_id=a.id,field_mapping={'shipper':'Sheet!A1','consignee':'Sheet!A2'})
        content=mbl.generate(db,actor,record['id'],'BO')
        check=load_workbook(BytesIO(content),data_only=False)
        assert check.active['A1'].value=='=1+1' and check.active['A1'].data_type=='s'
        assert check.active['A2'].value=='A & B <cargo>' and check.active['B1'].data_type=='f'


def test_hold_removal_updates_only_selected_members_and_supervisor_cancel(app):
    from app.documents import release,notifications
    from app.models import HoldFact
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app,hold=True)
        h=db.scalar(select(HoldFact).where(HoldFact.ticket_id==t['id']))
        removed=call(db,actor,release.remove_holds,members=[{'hold_id':h.id,'expected_version':h.version,'source_version':t['version']}],reason='Supplier confirmed settlement',removed_on='2026-10-31',confirmed_phone='+2347000000000',ngn_per_usd='1500')
        assert not release.hold_state(db,'ticket',t['id'])['active']
        assert db.get(Ticket,t['id']).phone_normalized=='+2347000000000'
        assert release.physical_totals(db,t['id'])['pieces']==0
        assert removed['next_action']=='arrival'
        assert removed['members'][0]['removal_ngn_per_usd']=='1500.000000'
        assert removed['members'][0]['rate_applied_to_invoice'] is True
        body=call(db,actor,notifications.render,kind='remove_hold',ticket_ids=[t['id']])['text']
        assert '2026-10-31' in body and 'removed ON HOLD by Supplier' in body
        ordinary=Actor(actor.id,{'release.read','release.hold'},{wh})
        with pytest.raises(DomainError):call(db,ordinary,release.cancel_removal,id=h.id,expected_version=h.version,reason='Correction')
        cancelled=call(db,actor,release.cancel_removal,id=h.id,expected_version=h.version,reason='Supervisor correction')
        assert cancelled['label']=='REMOVED ON CREDIT' and not cancelled['credit_authorized']
        with pytest.raises(DomainError):call(db,actor,notifications.render,kind='remove_hold',ticket_ids=[t['id']])


def test_actual_ngn_pcs_template_and_literal_xlsx(app):
    from app.documents import notifications
    from app.documents.projection import packing_projection
    from app.documents.rendering import render_artifact
    from test_logistics_review_2 import manual_ticket
    from app.packing import service as packing
    from openpyxl import load_workbook
    from io import BytesIO
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);doc,t=manual_ticket(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],mark='=HYPERLINK("https://invalid.example")',item_en='A & B <cargo>',contact_raw='+2347000000000',phone_normalized='+2347000000000',phone_confirmed=True,destination_warehouse_id=wh)
        t=call(db,actor,packing.arrive,id=t['id'],expected_version=t['version'],pieces=3,kg='30',cbm='1',occurred_on='2026-10-31',source='Arrival',mode='air')
        from app.tracking import service as tracking
        call(db,actor,tracking.set_node,target_type='ticket',target_id=t['id'],expected_version=t['version'],node='warehouse_arrived',mode='manual',destination_warehouse_id=wh,occurred_at='2026-11-01T10:00:00+08:00',source='Warehouse receipt')
        notice=call(db,actor,notifications.render,kind='arrival',entry='accounts',ticket_ids=[t['id']])
        assert 'NGN 2000' in notice['text'] and 'PER PCS' in notice['text']
        assert 'Clearance Fee' not in notice['text'] and '₦6000' in notice['text']
        receipt=call(db,actor,notifications.render,kind='receipt',ticket_ids=[t['id']])
        assert '**CBM**: 1.000000cbm' in receipt['text'] and '**Received Date**: 2026-11-01' in receipt['text']
        data=packing_projection(db,[db.get(Ticket,t['id'])]);content,_=render_artifact(app.state.settings,data,'xlsx','packing')
        book=load_workbook(BytesIO(content),data_only=False)
        assert book['Master']['C2'].data_type=='s' and book['Master']['C2'].value.startswith('=HYPERLINK')
        assert book['Master']['E2'].value=='A & B <cargo>'


def test_copy_requires_fresh_signature_date_and_rechecks_finance(app):
    from app.documents import notifications
    from app.models import Notification
    from datetime import timedelta
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        generation=call(db,actor,notifications.render,kind='arrival',ticket_ids=[t['id']])
        db.get(Notification,generation['id']).generated_on-=timedelta(days=1)
        with pytest.raises(DomainError,match='signature date'):
            call(db,actor,notifications.copy_success,id=generation['id'],expected_version=1,clipboard_success=True)
        limited=Actor(actor.id,{'documents.read','documents.notify'},{wh})
        with pytest.raises(DomainError):notifications.current_generation(db,limited,db.get(Notification,generation['id']))


def test_payment_registration_does_not_post_cash_and_color_is_precise(app):
    from app.documents import release
    from app.finance import service as finance
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        before=release.receivable_state(db,actor,t['id']);assert before['color']=='red'
        inv=finance.get_invoice(db,actor,t['invoice_id'])
        payment=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=db.get(Ticket,t['id']).party_id,amount='100',currency='NGN')
        call(db,actor,release.register_payment,invoice_id=inv['id'],expected_version=inv['version'],payment_id=payment['id'],source_version=payment['version'],source='Customer submitted payment intent')
        state=release.receivable_state(db,actor,t['id'])
        assert state['registered'] and state['color'] is None and state['invoice']['allocated_ngn']=='0.00'
        call(db,actor,release.issue_order,members=[{'ticket_id':t['id'],'expected_version':t['version']}],mode='credit',promise_on='2026-12-01',guarantor='Actual guarantor')
        assert release.receivable_state(db,actor,t['id'])['color']=='yellow'


def test_notification_http_image_and_ordinary_scope_denial(app):
    from app.documents import notifications
    from PIL import Image
    from io import BytesIO
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        generation=call(db,actor,notifications.render,kind='arrival',ticket_ids=[t['id']])
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        image=client.get('/api/notifications/'+generation['id']+'/image')
        assert image.status_code==200,image.text[:200]
        with Image.open(BytesIO(image.content)) as im:im.verify()
        client.post('/api/auth/login',json={'username':'operator','password':'synthetic-password-123'})
        assert client.get('/api/notifications/'+generation['id']+'/image').status_code==403


def test_group_copy_expands_original_members_and_new_member_invalidates_order(app):
    from app.documents import notifications,release
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        second=call(db,actor,packing.create_ticket,document_id=doc['id'],party_id=t['party_id'],receipt='SYN2',mark='SYN mark',pieces=1,kg='1',cbm='1',transport='AIR',freight_usd='0',clearing_ngn='0')
        generation=call(db,actor,notifications.render,kind='rates',ticket_ids=[t['id']])
        # Arrival cannot claim READY FOR PICK UP for an unarrived current group member.
        with pytest.raises(DomainError):call(db,actor,notifications.render,kind='arrival',ticket_ids=[t['id']])
        with pytest.raises(DomainError,match='every current member'):
            call(db,actor,release.issue_order,members=[{'ticket_id':t['id'],'expected_version':t['version']}],mode='credit',promise_on='2026-12-01',guarantor='Actual guarantor')


def test_nonempty_document_migration_refuses_downgrade(app):
    from alembic import command
    from app.cli import migration_config
    with app.state.session_factory() as db,db.begin():prepared(db,app,hold=True)
    cfg=migration_config();cfg.attributes['database_url']=app.state.settings.database_url
    with pytest.raises(RuntimeError,match='Cannot discard nonempty'):command.downgrade(cfg,'c11000300006')
    command.check(cfg)


def test_partial_arrival_notice_does_not_claim_unarrived_goods(app):
    from app.documents import notifications
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);doc,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],contact_raw='+2347000000000',phone_normalized='+2347000000000',phone_confirmed=True,destination_warehouse_id=wh)
        t=call(db,actor,packing.arrive,id=t['id'],expected_version=t['version'],pieces=1,kg='1',cbm='0.333333',occurred_on='2026-11-01',source='Only first actual batch',mode='air')
        from app.tracking import service as tracking
        call(db,actor,tracking.set_node,target_type='ticket',target_id=t['id'],expected_version=t['version'],node='warehouse_arrived',mode='manual',destination_warehouse_id=wh,occurred_at='2026-11-02T10:00:00+08:00',source='Only first actual batch received at warehouse')
        text=call(db,actor,notifications.render,kind='arrival',ticket_ids=[t['id']])['text']
        assert 'Packages: 1 PCS' in text and 'Volume: 0.333333 CBM' in text
        assert '₦495.00' in text and 'USD 0.33' in text


def test_photo_bytes_require_current_owner_and_hold_reads_are_scoped(app):
    from app.documents import release
    from app.models import CustomerIdentity,CustomerSession,utcnow
    from app.attachments import put_file
    from app.api.dependencies import token_hash
    from datetime import timedelta
    from PIL import Image
    from io import BytesIO
    from app.cargo import service as cargo
    from test_logistics import receipt_payload
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        call(db,actor,cargo.create_receipt,**receipt_payload(wh,t['party_id'],customer_phone='+2347000000000'))
        order=call(db,actor,release.issue_order,members=[{'ticket_id':t['id'],'expected_version':t['version']}],mode='credit',promise_on='2026-12-01',guarantor='Actual guarantor')
        b=BytesIO();Image.new('RGB',(12,12),'blue').save(b,format='PNG')
        a=put_file(db,actor,b.getvalue(),'pickup.png','image/png','user',actor.id)
        event=call(db,actor,release.physical_release,order_id=order['id'],expected_version=1,ticket_id=t['id'],pieces=1,kg='1',cbm='0.333333',occurred_at='2026-11-04T10:00:00+08:00',photo_ids=[a.id])
        for name,number,token in [('operator','+2347000000000','photo-owner'),('other','+2347000009999','photo-other')]:
            c=CustomerIdentity(user_id=db.scalar(select(User.id).where(User.username==name)),phone=number,verified_at=utcnow());db.add(c);db.flush()
            db.add(CustomerSession(customer_id=c.id,token_hash=token_hash(token),csrf_hash=token_hash('csrf'),expires_at=utcnow()+timedelta(hours=1)))
        photo_id=a.id
    path='/api/customer/physical-releases/'+event['id']+'/photos/'+photo_id
    with TestClient(app) as client:
        assert client.get(path).status_code==401
        client.cookies.set('mcstar_customer','photo-other');assert client.get(path).status_code==404
        client.cookies.set('mcstar_customer','photo-owner');assert client.get(path).content.startswith(b'\x89PNG')
        assert len(client.get('/api/customer/documents?limit=1&offset=0').json()['rows'])==1
        assert len(client.get('/api/customer/documents?limit=1&offset=1').json()['rows'])==1
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        assert client.get('/api/tickets/'+t['id']+'/holds').json()['ticket']['active'] is False
        assert client.get('/api/tickets/'+t['id']+'/notification-marks').json()==[]
        with app.state.session_factory() as db,db.begin():
            ticket=db.get(Ticket,t['id']);ticket.phone_normalized='+2347000009999';ticket.contact_raw=ticket.phone_normalized;ticket.version+=1
        assert client.get(path).status_code==404


def test_billing_arrival_alone_cannot_claim_warehouse_ready(app):
    from app.documents import notifications,release
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);doc,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],destination_warehouse_id=wh)
        t=call(db,actor,packing.arrive,id=t['id'],expected_version=t['version'],pieces=3,kg='3',cbm='1',occurred_on='2026-11-01',source='At port only; not warehouse receipt',mode='air')
        with pytest.raises(DomainError,match='warehouse'):
            call(db,actor,notifications.render,kind='arrival',ticket_ids=[t['id']])


def test_c6_still_independently_protects_ticket_without_phone_history(app):
    from alembic import command
    from app.cli import migration_config
    from app.models import CargoPhoneHistory
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);doc,t=make_document(db,actor,wh,party)
        assert not list(db.scalars(select(CargoPhoneHistory)))
    cfg=migration_config();cfg.attributes['database_url']=app.state.settings.database_url
    with pytest.raises(RuntimeError,match='Cannot discard pricing authority'):command.downgrade(cfg,'c11000300005')
    with app.state.session_factory() as db:
        assert db.get(Ticket,t['id']).pricing_authority=='totals'


def test_linked_native_owner_hold_blocks_ordinary_notice(app):
    from app.documents import notifications,release
    from app.cargo import service as cargo
    from app.packing import service as packing
    from test_logistics import receipt_payload
    with app.state.session_factory() as db,db.begin():
        actor,wh,doc,t=prepared(db,app)
        native=call(db,actor,cargo.create_receipt,**receipt_payload(wh,t['party_id'],customer_phone='ON HOLD BY SUPPLIER'))
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],native_receipt_id=native['id'])
        assert release.cargo_hold_state(db,db.get(Ticket,t['id']))['active']
        with pytest.raises(DomainError,match='manufacturer'):
            call(db,actor,notifications.render,kind='arrival',ticket_ids=[t['id']])


def test_profile_public_projection_is_explicit_and_versioned(app):
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        assert client.get('/api/documents/profile').json()['version']==0
        saved=client.post('/api/documents/profile',json={'expected_version':0,'bank_name':'INTERNAL SYNTHETIC BANK','account_number':'SYNTHETIC PRIVATE ACCOUNT','public_company_name':'Synthetic public company'},headers={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'profile-one'})
        assert saved.status_code==200 and client.get('/api/documents/profile').json()['version']==1
        public=client.get('/api/public/profile')
        assert public.json()=={'company_name':'Synthetic public company'}
        assert 'BANK' not in public.text and 'ACCOUNT' not in public.text
