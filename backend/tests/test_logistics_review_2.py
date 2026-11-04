"""Limited fix-round-2 regressions for manual pricing authority and native-link repair."""
import pytest
from fastapi.testclient import TestClient
from app.common import Actor,DomainError
from app.models import Ticket,QuoteSnapshot
from app.finance import service as finance
from app.packing import service as packing,imports
from app.pricing import service as pricing
from app.cargo import service as cargo
from test_logistics import who,call,seed,receipt_payload
from test_packing import make_document
from test_logistics_review import arrival,source_batch


def manual_ticket(db,actor,wh,party):
    d,t=make_document(db,actor,wh,party)
    t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=1,kg='30',destination='OLD',goods_class='general',received_on='2026-11-01',manual={'unit':'PCS','currency':'NGN','unit_price':'2000','source':'Synthetic manual unit rate'})
    return d,t


@pytest.mark.parametrize('paid',[False,True])
def test_n1_corrected_components_survive_destination_http(app,paid):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=manual_ticket(db,actor,wh,party);old_quote=t['quote_id']
        call(db,actor,pricing.create_card,transport='AIR',origin='GZ',goods_class='general',destination='NEW',unit='KG',currency='USD',unit_price='10',valid_from='2026-01-01',valid_to='2026-12-31',source='Synthetic default must not override manual totals')
        if paid:
            t=arrival(db,actor,t,1,'1','0.1')
            payment=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='1000',currency='NGN')
            call(db,actor,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':t['invoice_id'],'expected_version':1,'amount_ngn':'1000'}])
    changes={'charge_components':[{'component':'freight','currency':'NGN','unit':'PCS','amount':'9000','source':'Explicit reviewed total correction'}]}
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json();h={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'component-correction'}
        preview=client.post('/api/tickets/'+t['id']+'/change-preview',json=changes,headers=h)
        assert preview.status_code==200,preview.text
        corrected=client.patch('/api/tickets/'+t['id'],json=dict(changes,expected_version=t['version'],finance_preview=preview.json()['finance_preview'],reason='Reviewed source correction'),headers=h)
        assert corrected.status_code==200,corrected.text
        corrected=corrected.json();assert corrected['clearing_ngn']=='9000.00'
        h['Idempotency-Key']='destination-only'
        preview=client.post('/api/tickets/'+t['id']+'/change-preview',json={'destination':'NEW'},headers=h)
        assert preview.status_code==200,preview.text
        changed=client.patch('/api/tickets/'+t['id'],json={'expected_version':corrected['version'],'destination':'NEW','finance_preview':preview.json()['finance_preview'],'reason':'Destination metadata only'},headers=h)
        assert changed.status_code==200,changed.text
        changed=changed.json();assert changed['clearing_ngn']=='9000.00'
        assert corrected['quote_id'] is None and corrected['quote'] is None
        assert changed['quote'] is None and changed['pricing_authority']=='totals'
    with app.state.session_factory() as db:
        assert str(db.get(QuoteSnapshot,old_quote).charge_ngn)=='6000.00'
        if paid:
            assert changed['invoice_id']==t['invoice_id']
            assert finance.get_invoice(db,actor,t['invoice_id'])['amount_ngn']=='3000.00'
            assert finance.get_payment(db,actor,payment['id'])['amount_ngn']=='1000.00'
            assert finance.get_payment(db,actor,payment['id'])['available_ngn']=='0.00'


def test_n2_void_old_link_unlink_and_original_command_replay(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        request=dict(id=t['id'],expected_version=1,native_receipt_id=r['id'],idempotency_key='link-before-void')
        linked=packing.update_ticket(db,actor,request)
        call(db,actor,cargo.receipt_state,id=r['id'],expected_version=1,state='void',reason='Synthetic void of original source')
        assert packing.update_ticket(db,actor,request)==linked
        unlinked=call(db,actor,packing.update_ticket,id=t['id'],expected_version=linked['version'],native_receipt_id=None)
        assert unlinked['native_receipt_id'] is None and unlinked['previous_native_receipt_id']==r['id']
        assert packing.update_ticket(db,actor,request)==linked
        with pytest.raises(DomainError):packing.update_ticket(db,Actor(actor.id,{'packing.read','packing.manage'},{wh}),request)


@pytest.mark.parametrize('replacement',[False,True])
@pytest.mark.parametrize('new_link',[False,True])
def test_n2_authorized_legacy_wrong_warehouse_link_repair(app,replacement,new_link):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);other,other_party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        old=call(db,actor,cargo.create_receipt,**(receipt_payload(other,other_party)|{'series':'OLD'}))
        target=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party)) if new_link else None
        target_id=target['id'] if target else None
        db.get(Ticket,t['id']).native_receipt_id=old['id'];db.flush() # Synthetic pre-fix bad relationship, never allowed by current commands.
        limited=Actor(actor.id,{'packing.read','packing.manage','packing.import','cargo.read'},{wh})
        if replacement:
            batch=source_batch(db,actor,wh);row=next(r for r in batch['rows'] if r['eligible']);mapping=[{'row_id':row['id'],'ticket_id':t['id'],'values':{'native_receipt_id':target_id}}]
            with pytest.raises(DomainError) as denied:imports.replacement_preview(db,limited,d['id'],batch['id'],mapping)
            assert denied.value.status==403
            preview=imports.replacement_preview(db,actor,d['id'],batch['id'],mapping)
            request=dict(id=d['id'],expected_version=preview['version'],batch_id=batch['id'],mapping=mapping,preview=preview,reason='Repair historical wrong source',idempotency_key='legacy-replace')
            result=imports.replace_document(db,actor,request)
            assert result['tickets'][0]['native_receipt_id']==target_id
            assert imports.replace_document(db,actor,request)==result
            with pytest.raises(DomainError):imports.replace_document(db,limited,request)
        else:
            request=dict(id=t['id'],expected_version=1,native_receipt_id=target_id,idempotency_key='legacy-unlink')
            with pytest.raises(DomainError) as denied:packing.update_ticket(db,limited,request)
            assert denied.value.status==403
            result=packing.update_ticket(db,actor,request)
            assert result['previous_native_receipt_id']==old['id'] and result['native_receipt_id']==target_id
            assert packing.update_ticket(db,actor,request)==result
            with pytest.raises(DomainError):packing.update_ticket(db,limited,request)
        if target:
            call(db,actor,cargo.receipt_state,id=target_id,expected_version=1,state='void',reason='Later target lifecycle change')
            assert (imports.replace_document if replacement else packing.update_ticket)(db,actor,request)==result


def test_n1_totals_quantity_requires_explicit_semantic_choice_http(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=manual_ticket(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],charge_components=[{'component':'freight','currency':'NGN','unit':'PCS','amount':'9000','source':'Reviewed total, not inferred unit rate'}])
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json();h={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'quantity-without-decision'}
        rejected=client.patch('/api/tickets/'+t['id'],json={'expected_version':t['version'],'pieces':4,'kg':'40'},headers=h)
        assert rejected.status_code==409,rejected.text
        assert rejected.json()['error']['code']=='pricing_decision_required'
        h['Idempotency-Key']='retain-reviewed-total'
        retained=client.patch('/api/tickets/'+t['id'],json={'expected_version':t['version'],'pieces':4,'kg':'40','repricing':'retain_totals','reason':'Explicit total remains unchanged for revised quantities'},headers=h)
        assert retained.status_code==200,retained.text
        retained=retained.json();assert retained['clearing_ngn']=='9000.00' and retained['quote'] is None
        h['Idempotency-Key']='new-explicit-unit-rate'
        quoted=client.patch('/api/tickets/'+t['id'],json={'expected_version':retained['version'],'manual':{'unit':'PCS','currency':'NGN','unit_price':'3000','source':'Explicit replacement unit tariff'}},headers=h)
        assert quoted.status_code==200,quoted.text
        quoted=quoted.json();assert quoted['clearing_ngn']=='12000.00' and quoted['quote']['charge_ngn']=='12000.00'
        h['Idempotency-Key']='confirmed-rate-quantity'
        resized=client.patch('/api/tickets/'+t['id'],json={'expected_version':quoted['version'],'pieces':5,'kg':'50'},headers=h)
        assert resized.status_code==200,resized.text
        assert resized.json()['clearing_ngn']=='15000.00' and resized.json()['quote']['unit_price']=='3000.00'


def test_n1_explicit_quote_choice_uses_new_card_and_rejects_unavailable_quote(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=manual_ticket(db,actor,wh,party);old_quote=t['quote_id']
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],charge_components=[{'component':'freight','currency':'NGN','unit':'PCS','amount':'9000','source':'Reviewed total'}])
        with pytest.raises(DomainError) as missing:
            with db.begin_nested():call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],repricing='quote',destination='MISSING')
        assert missing.value.code=='quote_unavailable'
        assert packing.get_ticket(db,actor,t['id'])['clearing_ngn']=='9000.00'
        call(db,actor,pricing.create_card,transport='AIR',origin='GZ',goods_class='general',destination='NEW',unit='KG',currency='USD',unit_price='10',valid_from='2026-01-01',valid_to='2026-12-31',source='Synthetic selected new card')
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],repricing='quote',destination='NEW',fx={'direction':'NGN_PER_USD','rate':'1500'})
        assert t['pricing_authority']=='quote' and not t['quote']['manual'] and t['freight_usd']=='300.00' and t['clearing_ngn']=='0.00'
        assert db.get(QuoteSnapshot,old_quote).charge_ngn==6000


def test_n1_replacement_reviewed_components_supersede_old_quote(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);d,t=manual_ticket(db,actor,wh,party)
        b=source_batch(db,actor,wh);row=next(r for r in b['rows'] if r['eligible'])
        mapping=[{'row_id':row['id'],'ticket_id':t['id'],'values':{'freight_usd':'0','clearing_ngn':'9000','charge_components':[{'component':'freight','currency':'NGN','unit':'PCS','amount':'9000','source':'Reviewed replacement total'}]}}]
        preview=imports.replacement_preview(db,actor,d['id'],b['id'],mapping)
        d=call(db,actor,imports.replace_document,id=d['id'],expected_version=preview['version'],batch_id=b['id'],mapping=mapping,preview=preview,reason='Explicit file correction')
        t=d['tickets'][0];assert t['quote'] is None and t['pricing_authority']=='totals'
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],destination='NEW')
        assert t['clearing_ngn']=='9000.00' and t['quote'] is None


def test_n1_migration_review_preserves_ar_and_allows_metadata_then_explicit_resolution(app):
    from alembic import command
    from app.cli import migration_config
    from app.models import Shipment,PackingDocument
    from sqlalchemy import text
    from uuid import uuid4
    from datetime import date
    config=migration_config();config.attributes['database_url']=app.state.settings.database_url
    command.downgrade(config,'c11000300005')
    identity=str(uuid4())
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
        shipment=Shipment(warehouse_id=wh,reference='Synthetic pre-authority shipment',transport='AIR',origin='GZ');db.add(shipment);db.flush()
        doc=PackingDocument(warehouse_id=wh,shipment_id=shipment.id,reference='Synthetic legacy source',uploaded_on=date(2026,1,1));db.add(doc);db.flush()
        quote=QuoteSnapshot(warehouse_id=wh,manual=True,unit='PCS',currency='NGN',unit_price=2000,quantity=3,charge_usd=0,charge_ngn=6000,amount_ngn=6000,basis={'source':'Synthetic legacy unit snapshot'});db.add(quote);db.flush();quote_id=quote.id
        inv=call(db,actor,finance.create_invoice,warehouse_id=wh,party_id=party,reference='Legacy reviewed partial AR',amount_ngn='2000',charge_usd='0',charge_ngn='2000',uploaded_on='2026-01-01')
        payment=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='1000',currency='NGN')
        call(db,actor,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':inv['id'],'expected_version':1,'amount_ngn':'1000'}])
        db.execute(text("INSERT INTO tickets(id,version,created_at,updated_at,document_id,warehouse_id,party_id,position,receipt,phone_confirmed,transport,pieces,kg,cbm,freight_usd,clearing_ngn,quote_id,invoice_id,state,review_state) VALUES (:id,1,now(),now(),:doc,:wh,:party,1,'LEGACY',false,'AIR',3,30,3,0,6000,:quote,:invoice,'active','pending')"),dict(id=identity,doc=doc.id,wh=wh,party=party,quote=quote_id,invoice=inv['id']))
    command.upgrade(config,'head');command.check(config)
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json();h={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'legacy-meta'}
        t=client.get('/api/tickets/'+identity).json()
        assert t['pricing_authority']=='review' and t['quote'] is None and t['quote_id'] is None and t['historical_quote_id']==quote_id
        preview=client.post('/api/tickets/'+identity+'/change-preview',json={'destination':'NEW','contact_raw':'Synthetic corrected contact'},headers=h)
        assert preview.status_code==200 and preview.json()['finance_preview'] is None
        result=client.patch('/api/tickets/'+identity,json={'expected_version':1,'destination':'NEW','contact_raw':'Synthetic corrected contact'},headers=h)
        assert result.status_code==200,result.text
    with app.state.session_factory() as db,db.begin():
        t=packing.get_ticket(db,actor,identity);t=arrival(db,actor,t,1,'1','1')
        assert t['invoice_id']==inv['id'] and t['arrivals'][0]['clearing_ngn'] is None
        assert finance.get_invoice(db,actor,inv['id'])['amount_ngn']=='2000.00'
        changes={'charge_components':[{'component':'freight','currency':'NGN','unit':'PCS','amount':'6000','source':'Explicit reviewed legacy unit and total'}]}
        preview=packing.ticket_change_preview(db,actor,identity,changes)
        t=call(db,actor,packing.update_ticket,id=identity,expected_version=t['version'],finance_preview=preview['finance_preview'],reason='Resolve historical pricing authority',**changes)
        assert t['pricing_authority']=='totals' and t['quote_id'] is None
        assert finance.get_invoice(db,actor,inv['id'])['amount_ngn']=='2000.00'
        assert finance.get_payment(db,actor,payment['id'])['amount_ngn']=='1000.00' and finance.get_payment(db,actor,payment['id'])['available_ngn']=='0.00'
    with pytest.raises(RuntimeError,match='Cannot discard pricing authority'):command.downgrade(config,'c11000300005')
