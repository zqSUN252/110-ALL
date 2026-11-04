"""Task 5 independent-review regressions: real PG, files and domain commands."""
import json
from decimal import Decimal
from io import BytesIO
import pytest
from sqlalchemy import select,func
from app.common import DomainError
from app.models import LegacyBill,LegacyBillMember,LegacyShipment,Ticket,Invoice,Delivery,utcnow
from app.migration import service as migration
from app.finance import service as finance
from app.packing import service as packing
from app.documents import release
from test_migration import sample_source,applied
from test_finance import actor,seed,call


def sourced_group(app,db,source=None,resolve=True):
    from app.migration.source import parse_source
    db.info['settings']=app.state.settings;who=actor(db);wh,party=seed(db,who)
    parsed=parse_source(json.dumps(source or sample_source()).encode())
    migration.apply_source(db,who,parsed,migration.plan_source(parsed,'review-fixture','synthetic'))
    bill=db.scalar(select(LegacyBill));ship=db.scalar(select(LegacyShipment))
    if resolve:
        migration.resolve_bill(db,who,bill.id,dict(expected_version=bill.version,warehouse_id=wh,party_id=party,uploaded_on='2026-01-01',dates_source='synthetic actual upload evidence',source='synthetic confirmed original',idempotency_key='resolve-bill'))
    migration.resolve_shipment(db,who,ship.id,dict(expected_version=ship.version,warehouse_id=wh,uploaded_on='2026-01-01',source='synthetic confirmed owner',idempotency_key='resolve-ship'))
    return who,wh,party,bill,list(db.scalars(select(Ticket).order_by(Ticket.position)))


def test_i02_unknown_original_kg_cannot_erase_known_covered_arrival_cap(app):
    from app.tracking import service as tracking
    from app.attachments import put_file
    from PIL import Image
    source=sample_source();ships=json.loads(source['data']['logistics_shipments']);ships[0]['tickets']=ships[0]['tickets'][:1]
    ships[0]['tickets'][0].update(freightUSD=0,clearingNGN=100)
    source['data']['logistics_shipments']=json.dumps(ships)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,tickets=sourced_group(app,db,source);t=tickets[0];t.destination_warehouse_id=wh
        migration.resolve_charge_basis(db,who,t.id,dict(expected_version=t.version,source='confirmed CBM basis',charge_components=[{'component':'clearing','currency':'NGN','unit':'CBM','amount':'100','source':'synthetic original'}],idempotency_key='basis'))
        call(db,who,packing.arrive,id=t.id,expected_version=t.version,pieces=2,kg='8',cbm='1',occurred_on='2026-01-02',source='actual measured batch',mode='sea')
        call(db,who,tracking.set_node,target_type='ticket',target_id=t.id,expected_version=t.version,node='warehouse_arrived',mode='manual',destination_warehouse_id=wh,occurred_at='2026-01-03T10:00:00+08:00',source='actual warehouse coverage')
        order=call(db,who,release.issue_order,members=[{'ticket_id':t.id,'expected_version':t.version}],mode='credit',promise_on='2026-12-01',guarantor='synthetic')
        remaining=release.eligible_order(db,who,order['id'])['members'][0]['remaining']
        assert remaining['kg'] is not None and Decimal(remaining['kg'])==8
        image=BytesIO();Image.new('RGB',(4,4)).save(image,format='PNG');photo=put_file(db,who,image.getvalue(),'proof.png','image/png','user',who.id)
        for kg in ('1000',None):
            with pytest.raises(DomainError,match='remaining'):
                call(db,who,release.physical_release,order_id=order['id'],expected_version=1,ticket_id=t.id,pieces=2,kg=kg,cbm='1',occurred_at='2026-01-04T10:00:00+08:00',photo_ids=[photo.id])
        picked=call(db,who,release.physical_release,order_id=order['id'],expected_version=1,ticket_id=t.id,pieces=2,kg='8',cbm='1',occurred_at='2026-01-04T10:00:00+08:00',photo_ids=[photo.id])
        assert Decimal(picked['kg'])==8 and t.kg is None


def test_i03_partial_membership_keeps_all_known_duplicate_receipt_rows_reserved(app):
    source=sample_source();bills=json.loads(source['data']['logistics_bills']);bills[0]['ticketKeys']=['R','MISSING'];source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,tickets=sourced_group(app,db,source,resolve=False)
        assert db.scalar(select(func.count()).select_from(LegacyBillMember))==2
        assert bill.relation_state=='pending'
        for t in tickets:
            with pytest.raises(DomainError,match='group'):
                call(db,who,packing.update_ticket,id=t.id,expected_version=t.version,party_id=party,kg='1',freight_usd='0',clearing_ngn='100',charge_components=[{'component':'clearing','currency':'NGN','unit':'CBM','amount':'100','source':'synthetic'}])
        assert db.scalar(select(func.count()).select_from(Invoice))==0


def test_i04_ap_period_requires_explicit_source_and_unknown_is_visible(app):
    source=sample_source();bills=json.loads(source['data']['logistics_bills']);bills[0].update(type='ap',discount=0);source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,tickets=sourced_group(app,db,source)
        month=finance.payable_period(db,who,wh,party,'2026-01')
        assert month.get('unknown_period_count')==1
        assert Decimal(month['unknown_period_billed_ngn'])==100 and Decimal(month['unknown_period_historical_paid_ngn'])==40
        inv=db.get(Invoice,bill.native_id);assert inv.period is None
        request=dict(expected_version=bill.version,invoice_version=inv.version,period='2026-01',source='explicit original settlement agreement',idempotency_key='period-confirm')
        first=migration.resolve_period(db,who,bill.id,request)
        assert migration.resolve_period(db,who,bill.id,request)==first
        month=finance.payable_period(db,who,wh,party,'2026-01')
        assert Decimal(month['billed_ngn'])==100 and Decimal(month['historical_paid_ngn'])==40 and month['unknown_period_count']==0
        with pytest.raises(DomainError,match='changed'):
            migration.resolve_period(db,who,bill.id,request|{'period':'2026-02','idempotency_key':'stale-period'})


def test_i05_late_receipt_is_irreversible_backout_difference(app,monkeypatch):
    from app.migration.operations import backout_plan
    from app.channels.receipts import receipt_event
    from test_channels import configure
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,party=seed(db,who);config=configure(monkeypatch,who,wh)
        delivery=Delivery(session=config.assistant_session,recipient='synthetic-target',text='synthetic',state='submitted',receipt='synthetic-receipt',boundary={'instance':config.instance},actor_id=who.id)
        db.add(delivery);db.flush();cutoff=utcnow()
        receipt_event(db,{'session':config.assistant_session,'payload':{'id':'synthetic-receipt','chatId':'synthetic-target','ack':2}},config)
        assert delivery.state=='delivered'
        plan=backout_plan(db,cutoff)
        assert plan['state']=='reconciliation_required' and plan['overwrite_allowed'] is False


def test_i01_occupied_legacy_transaction_cannot_be_claimed_via_normal_xlsx(app,tmp_path):
    from test_migration110 import test_consistent110_native_occupied_claim_and_history_do_not_replay
    from app.models import BankAccount,BankEntry,BankClaim,EvidenceCase,BankSourceRow
    from app.bank import service as bank
    from app.attachments import put_file
    from openpyxl import Workbook
    test_consistent110_native_occupied_claim_and_history_do_not_replay(app,tmp_path)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);account=db.scalar(select(BankAccount))
        book=Workbook();sheet=book.active;sheet.title='Statement';sheet.append(['Date','Credit','Debit','Description','Reference']);sheet.append(['2026-01-01',100,0,'source','REF'])
        blob=BytesIO();book.save(blob);file=put_file(db,who,blob.getvalue(),'later.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','user',who.id)
        preview=call(db,who,bank.import_preview,account_id=account.id,attachment_id=file.id)
        result=call(db,who,bank.confirm_import,id=preview['id'],expected_version=1,account_version=account.version,sheets=['Statement'],date_order='dmy',trusted_reference=False)
        assert result['duplicate_candidates']==1
        entry=db.get(BankEntry,db.scalar(select(BankSourceRow.entry_id).where(BankSourceRow.file_id==preview['id'],BankSourceRow.entry_id.is_not(None))))
        case=EvidenceCase(warehouse_id=account.warehouse_id,scope='live',account_id=account.id,amount=100,currency='NGN',state='pending');db.add(case);db.flush()
        with pytest.raises(DomainError) as rejected:
            call(db,who,bank.review_case,id=case.id,expected_version=1,entry_id=entry.id,entry_version=entry.version,action='confirm_manual',reason='generic manual review is not identity adjudication')
        assert rejected.value.code=='legacy_identity_review'
        assert db.scalar(select(func.count()).select_from(BankClaim).where(BankClaim.state!='released'))==1
        from app.bank.identity import adjudicate
        original=db.get(BankEntry,db.scalar(select(BankClaim.entry_id)))
        payload=dict(left_id=original.id,right_id=entry.id,left_version=original.version,right_version=entry.version,account_version=account.version,expected_version=0,decision='same',source='synthetic source identity evidence',reason='same observation of original credit',idempotency_key='identity-same')
        ruled=adjudicate(db,who,payload)
        with pytest.raises(DomainError,match='identity'):
            call(db,who,bank.review_case,id=case.id,expected_version=1,entry_id=entry.id,entry_version=entry.version,action='confirm_manual',reason='same transaction remains occupied')
        # New original evidence establishes two genuinely separate equal-valued credits.
        payload.update(expected_version=ruled['version'],decision='distinct',source='synthetic independent transaction originals',reason='two distinct bank transaction identities',idempotency_key='identity-distinct')
        adjudicate(db,who,payload)
        call(db,who,bank.review_case,id=case.id,expected_version=1,entry_id=entry.id,entry_version=entry.version,action='confirm_manual',reason='separate original credit, identity explicitly adjudicated')
        assert db.scalar(select(func.count()).select_from(BankClaim).where(BankClaim.state!='released'))==2
        from app.models import User,Grant,WarehouseScope,BankIdentityDecision
        user=db.scalar(select(User).where(User.username=='operator'));uid=user.id
        for capability in ('bank.identity','bank.override','evidence.read','finance.read'):db.add(Grant(user_id=uid,capability=capability))
        db.add(WarehouseScope(user_id=uid,warehouse_id=account.warehouse_id))
        payload.update(expected_version=db.scalar(select(BankIdentityDecision)).version,left_version=original.version,right_version=entry.version,account_version=account.version)
        payload.pop('idempotency_key')
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'operator','password':'synthetic-password-123'}).json()
        headers={'X-CSRF-Token':login['csrf_token'],'Idempotency-Key':'identity-http'}
        path='/api/legacy/bank-identities/adjudicate'
        response=client.post(path,json=payload,headers=headers);assert response.status_code==200,response.text
        assert client.post(path,json=payload,headers=headers).json()==response.json()
        assert client.post(path,json=payload,headers=headers|{'Idempotency-Key':'identity-stale'}).status_code==409
        with app.state.session_factory() as db,db.begin():db.query(Grant).filter(Grant.user_id==uid,Grant.capability=='bank.identity').delete()
        assert client.post(path,json=payload,headers=headers).status_code==403


def test_i06_confirmed_single_member_group_can_be_revised(app):
    source=sample_source();ships=json.loads(source['data']['logistics_shipments']);ships[0]['tickets']=ships[0]['tickets'][:1];ships[0]['tickets'][0].update(freightUSD=0,clearingNGN=100)
    source['data']['logistics_shipments']=json.dumps(ships)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,tickets=sourced_group(app,db,source);t=tickets[0]
        from app.migration.group_revision import preview,confirm
        from app.models import LegacyBillApplication
        application=db.scalar(select(LegacyBillApplication));inv=db.get(Invoice,bill.native_id)
        p={'expected_version':bill.version,'invoice_version':inv.version,'application_version':application.version,'action':'revise','members':[{'ticket_id':t.id,'expected_version':t.version,'changes':{'clearing_ngn':'120','charge_components':[{'component':'clearing','currency':'NGN','unit':'CBM','amount':'120','source':'current reviewed charge'}]}}],'source':'explicit current whole group','reason':'correct confirmed total'}
        impact=preview(db,who,bill.id,p);assert Decimal(impact['new_total_ngn'])==120
        result=confirm(db,who,bill.id,p|{'preview':impact,'idempotency_key':'group-revise'})
        assert Decimal(result['invoice']['amount_ngn'])==120 and bill.amount==100 and bill.received==40 and inv.source_key=='legacy:'+bill.id
        with pytest.raises(DomainError,match='whole-group'):
            call(db,who,finance.adjust_invoice,id=inv.id,expected_version=inv.version,amount_ngn='150',reason='must use group',preview=finance.adjustment_preview(db,who,inv.id,'150'))


def test_i06_shared_group_revise_owner_void_recover_preserve_cash_and_physical_facts(app,monkeypatch):
    from app.migration.group_revision import preview,confirm
    from app.models import LegacyBillApplication,Payment,Allocation,InvoiceAdjustment,Party,PhysicalRelease,Arrival
    from app.tracking import service as tracking
    from app.attachments import put_file
    from PIL import Image
    source=sample_source();ships=json.loads(source['data']['logistics_shipments'])
    for t,amount in zip(ships[0]['tickets'],(40,60)):t.update(freightUSD=0,clearingNGN=amount)
    source['data']['logistics_shipments']=json.dumps(ships);bills=json.loads(source['data']['logistics_bills']);bills[0].update(received=80,discount=0);source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,tickets=sourced_group(app,db,source);inv=db.get(Invoice,bill.native_id);application=db.scalar(select(LegacyBillApplication))
        from app.models import BankAccount,BankAccountWarehouse,BankEntry,BankClaim,EvidenceCase,LegacyBankEvidence
        from app.bank import service as bank
        from app.migration.bank_evidence import bind,preview as bank_preview
        from datetime import date
        account=BankAccount(warehouse_id=wh,scope='live',bank_name='synthetic',account_number='synthetic',currency='NGN');db.add(account);db.flush();db.add(BankAccountWarehouse(account_id=account.id,warehouse_id=wh,source='synthetic'))
        entry=BankEntry(account_id=account.id,currency='NGN',credit=80,debit=0,transaction_date=date(2026,1,1),reference='original',description='source',fingerprint='c'*64,issues=[])
        case=EvidenceCase(account_id=account.id,warehouse_id=wh,scope='live',currency='NGN',amount=80,transaction_date=date(2026,1,1));db.add_all([entry,case]);db.flush()
        call(db,who,bank.review_case,id=case.id,expected_version=case.version,entry_id=entry.id,entry_version=entry.version,action='confirm_manual',reason='original evidence')
        claim=db.scalar(select(BankClaim));proof={'claim_id':claim.id,'claim_version':claim.version,'case_version':case.version,'entry_version':entry.version,'account_version':account.version,'control_owner_party_id':party,'members':[{'bill_id':bill.id,'expected_version':bill.version,'invoice_version':inv.version,'amount_ngn':'80','source':'explicit original cash'}],'source':'synthetic bank proof','reason':'same original received'}
        bind(db,who,proof|{'preview':bank_preview(db,who,proof),'idempotency_key':'group-bank-proof'})
        from test_channels import configure
        from app.channels.receipts import receipt_event
        config=configure(monkeypatch,who,wh)
        delivery=Delivery(actor_id=who.id,session=config.assistant_session,recipient='synthetic-target',text='historical synthetic notice',receipt='original-notice',state='submitted',boundary={'instance':config.instance});db.add(delivery);db.flush()
        receipt_event(db,{'session':config.assistant_session,'payload':{'id':'original-notice','chatId':'synthetic-target','ack':2}},config)
        payment=call(db,who,finance.create_payment,warehouse_id=wh,party_id=party,scope='live',amount='30',currency='NGN',paid_on='2026-01-03')
        pay=call(db,who,finance.post_payment,id=payment['id'],expected_version=payment['version'],approval='manual',allocations=[{'invoice_id':inv.id,'expected_version':inv.version,'amount_ngn':'20'}])
        assert Decimal(pay['available_ngn'])==10
        for t in tickets:
            migration.resolve_charge_basis(db,who,t.id,dict(expected_version=t.version,source='source unit evidence',charge_components=[{'component':'clearing','currency':'NGN','unit':'CBM','amount':str(t.clearing_ngn),'source':'original CBM line'}],idempotency_key='basis-'+t.id))
        for t in tickets:
            t.destination_warehouse_id=wh
            call(db,who,packing.arrive,id=t.id,expected_version=t.version,pieces=t.pieces,kg='8' if t==tickets[0] else None,cbm=str(t.cbm),occurred_on='2026-01-02',source='actual batch',mode='sea')
            call(db,who,tracking.set_node,target_type='ticket',target_id=t.id,expected_version=t.version,node='warehouse_arrived',mode='manual',destination_warehouse_id=wh,occurred_at='2026-01-03T10:00:00+08:00',source='actual warehouse coverage')
        t=tickets[0]
        order=call(db,who,release.issue_order,members=[{'ticket_id':t.id,'expected_version':t.version} for t in tickets],mode='credit',promise_on='2026-12-01',guarantor='synthetic')
        image=BytesIO();Image.new('RGB',(4,4)).save(image,format='PNG');photo=put_file(db,who,image.getvalue(),'proof.png','image/png','user',who.id)
        call(db,who,release.physical_release,order_id=order['id'],expected_version=1,ticket_id=t.id,pieces=2,kg='8',cbm='1',occurred_at='2026-01-04T10:00:00+08:00',photo_ids=[photo.id])
        def request(action='revise',**extras):
            return dict(expected_version=bill.version,invoice_version=inv.version,application_version=application.version,action=action,members=[{'ticket_id':t.id,'expected_version':t.version,'changes':{}} for t in tickets],source='reviewed full original group',reason='explicit group reconciliation',refund_new_allocations=True,**extras)
        p=request();p['members'][0]['changes']={'pieces':1,'repricing':'retain_totals','reason':'invalid reduction'}
        with pytest.raises(DomainError,match='actual'):preview(db,who,bill.id,p)
        p=request();p['members'][0]['changes']={'clearing_ngn':'60'}
        plan=preview(db,who,bill.id,p);assert Decimal(plan['new_total_ngn'])==120 and Decimal(plan['new_owner_outstanding_ngn'])==20
        first=confirm(db,who,bill.id,p|{'preview':plan,'idempotency_key':'raise-group'})
        assert confirm(db,who,bill.id,p|{'preview':plan,'idempotency_key':'raise-group'})==first
        assert inv.amount_ngn==120 and bill.received==80 and finance.total(db,Allocation,'amount_ngn',invoice_id=inv.id)==20
        new=call(db,who,finance.create_party,name='Explicit new owner',warehouse_id=wh);old=db.get(Party,party)
        p=request(party_id=new['id'],discount_total_ngn='0',owner_evidence={'old_party_id':party,'new_party_id':new['id'],'old_party_version':old.version,'new_party_version':new['version'],'warehouse_ids':[wh],'source':'explicit owner correction without transfer of original funds'})
        plan=preview(db,who,bill.id,p);assert Decimal(plan['frozen_received_ngn'])==80 and Decimal(plan['refund_native_ngn'])==20 and Decimal(plan['new_owner_outstanding_ngn'])==120
        confirm(db,who,bill.id,p|{'preview':plan,'idempotency_key':'owner-group'})
        assert migration.historical_received(db,inv.id)==0
        payment=db.get(Payment,payment['id']);assert payment.party_id==party and Decimal(finance.payment_json(db,payment)['available_ngn'])==30
        for action in ('void','recover'):
            p=request(action);plan=preview(db,who,bill.id,p);confirm(db,who,bill.id,p|{'preview':plan,'idempotency_key':action+'-group'})
        assert inv.amount_ngn==120 and inv.party_id==new['id'] and migration.historical_received(db,inv.id)==0
        assert bill.amount==100 and bill.received==80 and application.historical_owner_party_id==party
        assert db.scalar(select(func.count()).select_from(InvoiceAdjustment))==4
        assert db.scalar(select(func.count()).select_from(PhysicalRelease))==1 and db.scalar(select(func.count()).select_from(Arrival))==2
        assert finance.total(db,Allocation,'amount_ngn',invoice_id=inv.id)==0 and db.scalar(select(func.count()).select_from(Payment))==1
        assert claim.state=='historical_bound' and claim.payment_id is None and db.scalar(select(LegacyBankEvidence)).control_owner_party_id==party
        from app.migration.bank_evidence import detail
        assert detail(db,who,db.scalar(select(LegacyBankEvidence)).id)['control_owner_party_id']==party
        assert delivery.state=='delivered' and db.scalar(select(func.count()).select_from(BankClaim).where(BankClaim.state!='released'))==1


def test_i05_ordinary_cutover_cannot_activate_unreviewed_restore(app,tmp_path):
    from dataclasses import replace
    from uuid import uuid4
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from app.db import make_engine,session_factory
    from app.migration.backup import backup,restore
    from app.migration.operations import cutover
    bundle=tmp_path/'base';backup(app.state.settings,bundle)
    schema='restore_review_'+uuid4().hex
    with app.state.engine.begin() as conn:conn.execute(text(f'CREATE SCHEMA {schema}'))
    target=replace(app.state.settings,database_url=make_url(app.state.settings.database_url).update_query_dict({'options':f'-csearch_path={schema}'}).render_as_string(hide_password=False),private_root=tmp_path/'restored')
    engine=make_engine(target.database_url)
    try:
        restore(target,bundle)
        with session_factory(engine)() as db,db.begin():
            with pytest.raises(DomainError,match='restore'):
                cutover(db,actor(db),0,'bypass','must not bypass checkpoint review')
    finally:
        engine.dispose()
        with app.state.engine.begin() as conn:conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))


def test_i05_frozen_http_accepts_only_signed_receipt_facts(app,monkeypatch):
    import hmac,hashlib
    from fastapi.testclient import TestClient
    from test_channels import configure
    from app.migration.operations import cutover,backout_plan
    from app.models import DeliveryReceipt,ChannelMessage,Job
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,_=seed(db,who);config=configure(monkeypatch,who,wh)
        delivery=Delivery(actor_id=who.id,session=config.assistant_session,recipient='synthetic-target',text='synthetic',receipt='frozen-ack',state='submitted',boundary={'instance':config.instance,'generation':config.generation});db.add(delivery);db.flush();identity=delivery.id
        cutover(db,who,0,'frozen','synthetic controlled freeze',active=False);cutoff=utcnow()
    envelope={'event':'message.ack','session':config.assistant_session,'payload':{'id':'frozen-ack','chatId':'synthetic-target','ack':2}}
    def send(client,event,bad=False):
        body=json.dumps(event).encode();signature=hmac.new(config.webhook_key.encode(),body,'sha512').hexdigest()
        return client.post('/api/webhooks/waha',content=body,headers={'X-Webhook-Hmac':'bad' if bad else signature,'X-Webhook-Hmac-Algorithm':'sha512'})
    with TestClient(app) as client:
        response=send(client,envelope);assert response.status_code==200,response.text
        assert send(client,envelope,True).status_code==401
        assert send(client,envelope|{'event':'message'}).status_code==409
        assert send(client,envelope|{'event':'session.status'}).status_code==409
    with app.state.session_factory() as db:
        delivery=db.get(Delivery,identity)
        assert delivery.state=='delivered' and delivery.boundary=={'instance':config.instance,'generation':config.generation}
        assert db.scalar(select(func.count()).select_from(DeliveryReceipt))==1
        assert db.scalar(select(func.count()).select_from(ChannelMessage))==0 and db.scalar(select(func.count()).select_from(Job))==0
        assert backout_plan(db,cutoff)['state']=='reconciliation_required'


def test_missing_due_date_can_be_resolved_only_with_explicit_business_date_evidence(app):
    source=sample_source();bills=json.loads(source['data']['logistics_bills']);bills[0].pop('dueDate');source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,_=sourced_group(app,db,source,resolve=False)
        p=dict(expected_version=bill.version,warehouse_id=wh,party_id=party,source='confirmed owner',idempotency_key='dates')
        with pytest.raises(DomainError,match='date'):migration.resolve_bill(db,who,bill.id,p)
        result=migration.resolve_bill(db,who,bill.id,p|dict(uploaded_on='2026-01-03',due_on='2026-01-17',dates_source='actual upload record and explicitly agreed deadline'))
        inv=db.get(Invoice,result['native_id'])
        assert str(inv.uploaded_on)=='2026-01-03' and str(inv.due_on)=='2026-01-17'
        assert bill.raw['date']=='2026-01-01' and 'dueDate' not in bill.raw

@pytest.mark.parametrize('late_stage',['none','during','after'])
def test_i05_bound_checkpoint_real_restore_rechecks_late_facts(app,tmp_path,monkeypatch,late_stage):
    from uuid import uuid4
    from dataclasses import replace
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from app.db import make_engine,session_factory
    from app.models import RecoveryCheckpoint,AppSetting
    from app.migration import backup as backup_module
    from app.migration.operations import checkpoint,cutover,backout_plan,backout_restore
    from test_channels import configure
    from app.channels.receipts import receipt_event
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,_=seed(db,who);config=configure(monkeypatch,who,wh)
        db.add(Delivery(actor_id=who.id,session=config.assistant_session,recipient='synthetic-target',text='synthetic',receipt='checkpoint-ack',state='submitted',boundary={'instance':config.instance}))
    bundle=tmp_path/'bound-base';manifest=backup_module.backup(app.state.settings,bundle)
    with app.state.session_factory() as db,db.begin():
        who=actor(db)
        with pytest.raises(DomainError,match='watermark'):checkpoint(db,who,'missing cutpoint','bad-checkpoint',{'revision':manifest['revision']})
        cp=checkpoint(db,who,'original cutpoint','checkpoint',manifest)
        assert checkpoint(db,who,'original cutpoint','checkpoint',manifest)==cp
        cutover(db,who,0,'frozen','drained synthetic cutpoint',active=False)
        row=db.get(RecoveryCheckpoint,cp['id']);plan=backout_plan(db,row.cutoff,row.id)
        assert plan['new_events']==0 and plan['state']=='no_new_business_events'
    def late_receipt():
        with app.state.session_factory() as db,db.begin():
            receipt_event(db,{'session':config.assistant_session,'payload':{'id':'checkpoint-ack','chatId':'synthetic-target','ack':2}},config)
    if late_stage=='during':
        actual_restore=backup_module.restore
        def restore_with_late_receipt(*args,**kwargs):
            result=actual_restore(*args,**kwargs);late_receipt();return result
        monkeypatch.setattr(backup_module,'restore',restore_with_late_receipt)
    schema='restore_checkpoint_'+uuid4().hex
    target=replace(app.state.settings,database_url=make_url(app.state.settings.database_url).update_query_dict({'options':f'-csearch_path={schema}'}).render_as_string(hide_password=False))
    engine=make_engine(target.database_url)
    try:
        with pytest.raises(DomainError,match='version'):
            backout_restore(app.state.settings,bundle,cp['id'],cp['version']+1,plan,schema,drained=True)
        result=backout_restore(app.state.settings,bundle,cp['id'],cp['version'],plan,schema,drained=True)
        assert result['restore_verified'] and not result['activated']
        assert result['state']==('reconciliation_required' if late_stage=='during' else 'isolated_restore_reviewed')
        if late_stage=='after':late_receipt()
        with session_factory(engine)() as db,db.begin():
            writer=db.scalar(select(AppSetting).where(AppSetting.key=='migration.writer'))
            assert writer.value['state']=='frozen'
            if late_stage=='none':
                assert cutover(db,actor(db),writer.version,'reviewed','same original restore checkpoint')['state']=='active'
            else:
                with pytest.raises(DomainError,match='restore|Source facts'):
                    cutover(db,actor(db),writer.version,'bypass','normal cutover cannot bypass late fact')
                assert writer.value['state']=='frozen'
    finally:
        engine.dispose()
        with app.state.engine.begin() as conn:conn.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))


def test_i06_historical_discount_excess_freezes_and_void_recover_retains_source(app):
    from app.migration.group_revision import preview,confirm
    from app.models import LegacyBillApplication,Payment
    source=sample_source();ships=json.loads(source['data']['logistics_shipments']);ships[0]['tickets']=ships[0]['tickets'][:1];ships[0]['tickets'][0].update(freightUSD=0,clearingNGN=100)
    source['data']['logistics_shipments']=json.dumps(ships);bills=json.loads(source['data']['logistics_bills']);bills[0].update(received=80,discount=20);source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,tickets=sourced_group(app,db,source);t=tickets[0];inv=db.get(Invoice,bill.native_id);application=db.scalar(select(LegacyBillApplication))
        migration.resolve_charge_basis(db,who,t.id,dict(expected_version=t.version,source='actual unit',charge_components=[{'component':'clearing','currency':'NGN','unit':'CBM','amount':'100','source':'source'}],idempotency_key='basis'))
        def request(action='revise',amount='100'):
            return dict(expected_version=bill.version,invoice_version=inv.version,application_version=application.version,action=action,members=[dict(ticket_id=t.id,expected_version=t.version,changes={'clearing_ngn':amount})],source='explicit full group',reason='synthetic correction')
        p=request(amount='90');plan=preview(db,who,bill.id,p)
        assert plan['pending_reconciliation'] and Decimal(plan['historical_excess_ngn'])==10
        with pytest.raises(DomainError,match='historical excess'):confirm(db,who,bill.id,p|{'preview':plan,'idempotency_key':'excess'})
        for action in ('void','recover'):
            p=request(action);plan=preview(db,who,bill.id,p);confirm(db,who,bill.id,p|{'preview':plan,'idempotency_key':action})
        assert inv.amount_ngn==100 and migration.historical_received(db,inv.id)==80 and migration.historical_discount(db,inv.id)==20
        assert Decimal(finance.invoice_json(db,inv)['outstanding_ngn'])==0 and bill.discount==20 and bill.received==80
        assert db.scalar(select(func.count()).select_from(Payment))==0


def test_i03_explicit_membership_confirms_partial_group_without_duplicate_ar(app):
    from app.models import LegacyTicket,LegacyResolution
    source=sample_source();bills=json.loads(source['data']['logistics_bills']);bills[0]['ticketKeys']=['R','MISSING'];source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,tickets=sourced_group(app,db,source,resolve=False)
        legacy=list(db.scalars(select(LegacyTicket)));p=dict(expected_version=bill.version,ticket_ids=[t.id for t in legacy],member_versions={t.id:t.version for t in legacy},source='actual whole group register',reason='missing key independently reviewed as cancelled source reference',idempotency_key='members')
        result=migration.resolve_members(db,who,bill.id,p);assert migration.resolve_members(db,who,bill.id,p)==result
        migration.resolve_bill(db,who,bill.id,dict(expected_version=bill.version,warehouse_id=wh,party_id=party,uploaded_on='2026-01-01',dates_source='actual upload record',source='confirmed owner',idempotency_key='bill'))
        assert {t.invoice_id for t in tickets}=={bill.native_id}
        assert db.scalar(select(func.count()).select_from(Invoice))==1 and bill.raw['ticketKeys']==['R','MISSING']
        assert db.scalar(select(LegacyResolution).where(LegacyResolution.entity_type=='bill_members')).links['original_declared_keys']==['R','MISSING']


def test_i03_ambiguous_shipments_keep_candidates_and_v1_upgrade_is_explicit(app):
    from app.models import MigrationBatch,LegacyTicket
    from app.migration.source import parse_source,digest
    from sqlalchemy import delete
    source=sample_source();ships=json.loads(source['data']['logistics_shipments']);ships.append(dict(ships[0],id='s2'));source['data']['logistics_shipments']=json.dumps(ships)
    parsed=parse_source(json.dumps(source).encode());plan=migration.plan_source(parsed,'review-v1','synthetic')
    with app.state.session_factory() as db,db.begin():
        who=actor(db);db.info['settings']=app.state.settings
        result=migration.apply_source(db,who,parsed,plan);bill=db.scalar(select(LegacyBill));batch=db.get(MigrationBatch,result['batch_id'])
        assert bill.shipment_id is None and bill.relation_state=='pending'
        assert db.scalar(select(func.count()).select_from(LegacyBillMember))==4
        old=dict(plan);old.pop('plan_hash');old['parser_version']=1;old_hash=digest(old)
        # Explicitly reconstruct the synthetic pre-fix v1 persisted shape.
        db.execute(delete(LegacyBillMember));batch.plan_hash=old_hash;db.flush()
        with pytest.raises(DomainError,match='reviewed mapping'):migration.apply_source(db,who,parsed,plan)
        with pytest.raises(DomainError,match='changed'):migration.upgrade_mapping(db,who,parsed,plan,'wrong','source-reviewed upgrade')
        upgraded=migration.upgrade_mapping(db,who,parsed,plan,old_hash,'source-reviewed upgrade')
        assert upgraded['added_members']==4
        assert migration.upgrade_mapping(db,who,parsed,plan,old_hash,'source-reviewed upgrade')['added_members']==0
        assert migration.apply_source(db,who,parsed,plan)['created']==0
        verified=migration.verify_source(db,parsed,batch.id);assert verified['relations_match'] and verified['relation_members']==4
        assert db.scalar(select(func.count()).select_from(LegacyTicket))==4


def test_i04_ap_original_date_and_period_http_with_current_scope_and_replay_auth(app):
    from fastapi.testclient import TestClient
    from app.models import Grant,WarehouseScope,User
    source=sample_source();bills=json.loads(source['data']['logistics_bills']);bills[0].update(type='ap',discount=0);source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,_=sourced_group(app,db,source,resolve=False)
        migration.resolve_bill(db,who,bill.id,dict(expected_version=bill.version,warehouse_id=wh,party_id=party,source='original bill dates and actual owner',idempotency_key='ap-bill'))
        inv=db.get(Invoice,bill.native_id);assert str(inv.uploaded_on)==bill.raw['date'] and inv.pricing['date_semantics']=='original_bill_date'
        identity=bill.id;payload=dict(expected_version=bill.version,invoice_version=inv.version,period='2026-01',source='actual settlement period agreement')
        user=db.scalar(select(User).where(User.username=='operator'));uid=user.id
        for cap in ('legacy.resolve','legacy.global_read','finance.read','payables.manage'):db.add(Grant(user_id=uid,capability=cap))
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'operator','password':'synthetic-password-123'}).json();headers={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'period-http'}
        path='/api/legacy/bills/'+identity+'/period'
        assert client.post(path,json=payload,headers=headers).status_code==403
        with app.state.session_factory() as db,db.begin():db.add(WarehouseScope(user_id=uid,warehouse_id=wh))
        response=client.post(path,json=payload,headers=headers);assert response.status_code==200,response.text
        assert client.post(path,json=payload,headers=headers).json()==response.json()
        assert client.post(path,json=payload|{'period':'2026-02'},headers=headers|{'Idempotency-Key':'stale'}).status_code==409
        with app.state.session_factory() as db,db.begin():db.query(Grant).filter(Grant.user_id==uid,Grant.capability=='payables.manage').delete()
        assert client.post(path,json=payload,headers=headers).status_code==403


def test_i06_group_http_current_scope_late_fact_and_revoked_replay(app):
    from fastapi.testclient import TestClient
    from app.models import LegacyBillApplication,Grant,WarehouseScope,User
    source=sample_source();ships=json.loads(source['data']['logistics_shipments']);ships[0]['tickets']=ships[0]['tickets'][:1];ships[0]['tickets'][0].update(freightUSD=0,clearingNGN=100);source['data']['logistics_shipments']=json.dumps(ships)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,tickets=sourced_group(app,db,source);t=tickets[0];inv=db.get(Invoice,bill.native_id);application=db.scalar(select(LegacyBillApplication));identity=bill.id
        payload=dict(expected_version=bill.version,invoice_version=inv.version,application_version=application.version,action='revise',members=[dict(ticket_id=t.id,expected_version=t.version,changes={'clearing_ngn':'120','charge_components':[{'component':'clearing','currency':'NGN','unit':'CBM','amount':'120','source':'actual unit evidence'}]})],source='full group source',reason='current actual change')
        user=db.scalar(select(User).where(User.username=='operator'));uid=user.id
        for cap in ('legacy.resolve','legacy.global_read','finance.read','finance.adjust','finance.invoice','finance.discount','packing.manage','packing.read'):db.add(Grant(user_id=uid,capability=cap))
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'operator','password':'synthetic-password-123'}).json();headers={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'group-http'};path='/api/legacy/bills/'+identity
        assert client.post(path+'/group-preview',json=payload,headers=headers).status_code==403
        with app.state.session_factory() as db,db.begin():db.add(WarehouseScope(user_id=uid,warehouse_id=wh))
        response=client.post(path+'/group-preview',json=payload,headers=headers);assert response.status_code==200,response.text
        prior=response.json()
        with app.state.session_factory() as db,db.begin():db.add(Delivery(actor_id=uid,session='synthetic',recipient='synthetic',text='actual completed observation',state='delivered'))
        assert client.post(path+'/group-confirm',json=payload|{'preview':prior},headers=headers).status_code==409
        fresh=client.post(path+'/group-preview',json=payload,headers=headers).json()
        response=client.post(path+'/group-confirm',json=payload|{'preview':fresh},headers=headers);assert response.status_code==200,response.text
        assert client.post(path+'/group-confirm',json=payload|{'preview':fresh},headers=headers).json()==response.json()
        with app.state.session_factory() as db,db.begin():db.query(Grant).filter(Grant.user_id==uid,Grant.capability=='finance.discount').delete()
        assert client.post(path+'/group-confirm',json=payload|{'preview':fresh},headers=headers).status_code==403


def test_i04_i06_ap_group_revision_preserves_confirmed_period_and_customer_ar(app):
    from app.migration.group_revision import preview,confirm
    from app.models import LegacyBillApplication
    source=sample_source();bills=json.loads(source['data']['logistics_bills']);bills[0].update(type='ap',discount=0);source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,tickets=sourced_group(app,db,source);inv=db.get(Invoice,bill.native_id);application=db.scalar(select(LegacyBillApplication))
        migration.resolve_period(db,who,bill.id,dict(expected_version=bill.version,invoice_version=inv.version,period='2026-01',source='actual AP agreement',idempotency_key='period'))
        p=dict(expected_version=bill.version,invoice_version=inv.version,application_version=application.version,action='revise',members=[],amount_ngn='120',source='full AP cost source',reason='explicit AP correction')
        confirm(db,who,bill.id,p|{'preview':preview(db,who,bill.id,p),'idempotency_key':'ap-revision'})
        report=finance.payable_period(db,who,wh,party,'2026-01')
        assert Decimal(report['billed_ngn'])==120 and Decimal(report['historical_paid_ngn'])==40 and inv.period=='2026-01'
        assert all(t.invoice_id is None for t in tickets) and bill.amount==100


@pytest.mark.parametrize('fact',['application','checkpoint','revision','identity'])
def test_f6_nonempty_review_facts_refuse_downgrade(app,fact):
    from app.models import LegacyBillApplication,RecoveryCheckpoint,LegacyGroupRevision,BankAccount,BankEntry,BankIdentityDecision
    from app.cli import migration_config
    from alembic import command
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,_=sourced_group(app,db)
        if fact=='application':db.scalar(select(LegacyBillApplication)).applied_received=0
        elif fact=='checkpoint':db.add(RecoveryCheckpoint(actor_id=who.id,source='synthetic preserved checkpoint',cutoff=utcnow(),state='captured',facts={},writer={}))
        elif fact=='revision':db.add(LegacyGroupRevision(bill_id=bill.id,actor_id=who.id,action='revise',source='synthetic immutable correction',reason='guard fixture',before={},after={},preview={}))
        else:
            from datetime import date
            account=BankAccount(warehouse_id=wh,scope='live',bank_name='synthetic',account_number='synthetic',currency='NGN');db.add(account);db.flush()
            entries=[BankEntry(account_id=account.id,currency='NGN',credit=1,debit=0,transaction_date=date(2026,1,1),reference='synthetic',description='source',fingerprint=str(i)*64,issues=[]) for i in (1,2)];db.add_all(entries);db.flush()
            db.add(BankIdentityDecision(left_id=entries[0].id,right_id=entries[1].id,decision='distinct',basis={},actor_id=who.id,source='separate originals',reason='preserve ruling'))
    config=migration_config();config.attributes['database_url']=app.state.settings.database_url
    with pytest.raises(RuntimeError,match='Refuse loss'):command.downgrade(config,'f11000500005')
    from alembic.script import ScriptDirectory
    with app.state.engine.connect() as conn:assert conn.execute(__import__('sqlalchemy').text('SELECT version_num FROM alembic_version')).scalar_one()==ScriptDirectory.from_config(config).get_current_head()


@pytest.mark.parametrize('standalone',[False,True])
def test_i03_ap_same_number_reservation_released_only_by_explicit_source_resolution(app,standalone):
    from app.models import LegacyShipment,Shipment
    from app.shipments import service as shipment_service
    from fastapi.testclient import TestClient
    source=sample_source();ships=json.loads(source['data']['logistics_shipments']);ships.append(dict(ships[0],id='s2'));source['data']['logistics_shipments']=json.dumps(ships)
    bills=json.loads(source['data']['logistics_bills']);bills[0].update(type='ap',discount=0,ticketKeys=[]);source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        who,wh,party,bill,_=sourced_group(app,db,source,resolve=False)
        sources=list(db.scalars(select(LegacyShipment).order_by(LegacyShipment.source_key)))
        for s in sources:
            if not s.native_id:migration.resolve_shipment(db,who,s.id,dict(expected_version=s.version,warehouse_id=wh,uploaded_on='2026-01-01',source='explicit original row identity',idempotency_key='ship-'+s.id))
        for s in sources:
            with pytest.raises(DomainError,match='Historical payable'):
                shipment_service.clearing_sync(db,who,db.get(Shipment,s.native_id),{'clearing_amount_ngn':'100'})
        identity=bill.id;selected=sources[0];other_id=sources[1].native_id
        payload=dict(expected_version=bill.version,ticket_ids=[],member_versions={},standalone=standalone,source='actual AP shipment register',reason='explicit original row or independent standalone payable')
        if not standalone:payload.update(shipment_id=selected.id,shipment_version=selected.version)
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json();headers={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'ap-members'}
        if not standalone:
            assert client.post('/api/legacy/bills/'+identity+'/members',json=payload|{'shipment_version':payload['shipment_version']+1},headers=headers|{'Idempotency-Key':'stale-ap-source'}).status_code==409
        response=client.post('/api/legacy/bills/'+identity+'/members',json=payload,headers=headers);assert response.status_code==200,response.text
        assert client.post('/api/legacy/bills/'+identity+'/members',json=payload,headers=headers).json()==response.json()
    with app.state.session_factory() as db,db.begin():
        who=actor(db);bill=db.get(LegacyBill,identity)
        migration.resolve_bill(db,who,bill.id,dict(expected_version=bill.version,warehouse_id=wh,party_id=party,period='2026-03',source='actual owner and separate actual period agreement',idempotency_key='ap-native'))
        inv=db.get(Invoice,bill.native_id);assert inv.period=='2026-03' and inv.amount_ngn==100 and migration.historical_received(db,inv.id)==40
        assert bill.source_shipment_ref=='SHIP' and bill.raw['shipmentNo']=='SHIP'
        for s in db.scalars(select(LegacyShipment).order_by(LegacyShipment.source_key)):
            native=db.get(Shipment,s.native_id)
            if not standalone and s.id==bill.shipment_id:
                assert native.clearing_invoice_id==inv.id
                with pytest.raises(DomainError,match='Historical payable'):shipment_service.clearing_sync(db,who,native,{'clearing_amount_ngn':'110'})
            else:
                result=call(db,who,shipment_service.update_shipment,id=native.id,expected_version=native.version,clearing_party_id=party,clearing_on='2026-03-01',clearing_amount_ngn='30')
                assert result['clearing_invoice_id']!=inv.id
        assert db.scalar(select(func.count()).select_from(Invoice))==(3 if standalone else 2)
