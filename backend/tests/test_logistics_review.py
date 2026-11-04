from decimal import Decimal
from io import BytesIO
import pytest
from openpyxl import Workbook
from fastapi.testclient import TestClient
from sqlalchemy import select,delete
from app.common import Actor,DomainError
from app.models import User,Grant,WarehouseScope,Ticket,Invoice,DocumentRevision,PartyWarehouse
from app.finance import service as finance
from app.packing import service as packing,imports
from app.pricing import service as pricing
from test_logistics import who,call,seed,receipt_payload
from test_packing import make_document,upload


def arrival(db,actor,t,pieces=1,kg='1',cbm='0.25'):
    return call(db,actor,packing.arrive,id=t['id'],expected_version=t['version'],pieces=pieces,kg=kg,cbm=cbm,occurred_on='2026-11-04',source='Synthetic actual batch',mode='sea' if t['transport']=='SEA' else 'air')


def test_i1_sea_card_unknown_clearing_survives_ticket_and_arrival(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,_=make_document(db,actor,wh,party,'SEA')
        call(db,actor,pricing.create_card,transport='SEA',origin='GZ',goods_class='test',destination='TEST',unit='CBM',currency='USD',unit_price='10',clearing_per_cbm_ngn=None,valid_from='2026-01-01',valid_to='2026-12-31',source='Synthetic confirmed freight only')
        t=call(db,actor,packing.create_ticket,document_id=d['id'],receipt='SYN2',party_id=party,pieces=1,kg='1',cbm='1',goods_class='test',destination='TEST',received_on='2026-11-01')
        assert t['clearing_ngn'] is None and t['quote']['charge_ngn'] is None and t['quote']['amount_ngn'] is None
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],ngn_per_usd='1500')
        t=arrival(db,actor,t,cbm='1');assert t['invoice_id'] is None
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],clearing_ngn='0')
        assert finance.get_invoice(db,actor,t['invoice_id'])['amount_ngn']=='15000.00'


def test_i2_manual_pcs_uses_pieces_not_kg(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=1,kg='30',manual={'unit':'PCS','currency':'NGN','unit_price':'2000','source':'Reviewed synthetic piece rate'})
        t=arrival(db,actor,t,kg='1',cbm='0.1')
        assert t['arrivals'][0]['clearing_ngn']=='2000.00'
        assert finance.get_invoice(db,actor,t['invoice_id'])['amount_ngn']=='2000.00'


@pytest.mark.parametrize('usd,ngn', [('0.02','0'),('0','0.02'),('0.02','0.02')])
def test_i3_four_tiny_batches_nonnegative_and_conserved(app,usd,ngn):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=1,pieces=4,kg='4',freight_usd=usd,clearing_ngn=ngn,ngn_per_usd='1.5')
        for i in range(4):
            t=arrival(db,actor,t)
            for field,total in [('freight_usd',usd),('clearing_ngn',ngn)]:
                observed=[Decimal(a[field]) for a in t['arrivals']]
                assert min(observed)>=0 and sum(observed)<=Decimal(total)
        assert sum(Decimal(a['freight_usd']) for a in t['arrivals'])==Decimal(usd)
        assert sum(Decimal(a['clearing_ngn']) for a in t['arrivals'])==Decimal(ngn)
        assert Decimal(finance.get_invoice(db,actor,t['invoice_id'])['amount_ngn'])==Decimal(usd)*Decimal('1.5')+Decimal(ngn)


def source_batch(db,actor,wh,kind='packing',duplicate=False):
    book=Workbook();sheet=book.active;sheet.title='FULL'
    sheet.append(['RECEIPT','DESCRIPTION','PCS','CBM','KG','FREIGHT USD','CLEARING NGN'])
    sheet.append(['SYN1','Synthetic original',3,1,3,1,0])
    if duplicate:
        detail=book.create_sheet('CUSTOMER');detail.append(['RECEIPT','DESCRIPTION','PCS','CBM','KG','FREIGHT USD','CLEARING NGN']);detail.append(['SYN1','Synthetic original',3,1,3,1,0])
    stream=BytesIO();book.save(stream)
    return call(db,actor,imports.preview_import,warehouse_id=wh,attachment_id=upload(db,actor,stream.getvalue()),kind=kind)


def operator_grants(db,wh,caps):
    uid=db.scalar(select(User.id).where(User.username=='operator'))
    db.execute(delete(Grant).where(Grant.user_id==uid))
    db.add_all([Grant(user_id=uid,capability=c) for c in caps]);db.add(WarehouseScope(user_id=uid,warehouse_id=wh))
    return uid


@pytest.mark.parametrize('route', ['change','void','recover','replacement','ticket-void'])
def test_i4_preview_requires_current_domain_read_http(app,route):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        batch=source_batch(db,actor,wh);row=next(r for r in batch['rows'] if r['eligible'])
        operator_grants(db,wh,['packing.manage','packing.import'])
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'operator','password':'synthetic-password-123'}).json();h={'X-CSRF-Token':auth['csrf_token']}
        if route=='change':r=client.post('/api/tickets/'+t['id']+'/change-preview',json={},headers=h)
        elif route=='replacement':r=client.post('/api/packing/documents/'+d['id']+'/replacement-preview',json={'batch_id':batch['id'],'mapping':[{'row_id':row['id'],'ticket_id':t['id']}]},headers=h)
        elif route=='ticket-void':r=client.get('/api/tickets/'+t['id']+'/void-preview')
        else:r=client.get('/api/packing/documents/'+d['id']+'/'+route+'-preview')
        assert r.status_code==403,r.text


def test_i5_replace_split_history_does_not_leak_cash(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=arrival(db,actor,t,3,'3','1')
        other_wh,_=seed(db,actor);db.add(PartyWarehouse(party_id=party,warehouse_id=other_wh,active=True,source="Synthetic reviewed participation"));db.flush()
        pay=call(db,actor,finance.create_payment,warehouse_id=other_wh,party_id=party,amount='1500',currency='NGN')
        call(db,actor,finance.post_payment,id=pay['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':t['invoice_id'],'expected_version':1,'amount_ngn':'1500'}])
        batch=source_batch(db,actor,wh);row=next(r for r in batch['rows'] if r['eligible']);mapping=[{'row_id':row['id'],'ticket_id':t['id']}]
        preview=imports.replacement_preview(db,actor,d['id'],batch['id'],mapping)
        d=call(db,actor,imports.replace_document,id=d['id'],expected_version=preview['version'],batch_id=batch['id'],mapping=mapping,preview=preview,reason='Synthetic reviewed replacement')
        call(db,actor,packing.split_document,id=d['id'],expected_version=d['version'],ticket_ids=[t['id']],reference='Synthetic split')
        limited=Actor(actor.id,{'packing.read'},{wh})
        result=packing.get_document(db,limited,d['id'])
        assert result['tickets']==[]
        assert 'finance_preview' not in str(result['revisions'])
        stored=db.scalar(select(DocumentRevision).where(DocumentRevision.document_id==d['id']))
        assert stored.difference['changes'][0]['finance_preview']['original_payment_cash_ngn']=='1500.00'
        assert set(stored.difference['changes'][0]['financial_sources']['warehouse_ids'])=={wh,other_wh}
        with pytest.raises(DomainError):packing.get_document_history(db,limited,d['id'])
        with pytest.raises(DomainError):packing.get_document_history(db,Actor(actor.id,{'packing.read','finance.read'},{wh}),d['id'])
        history=packing.get_document_history(db,Actor(actor.id,{'packing.read','finance.read'},{wh,other_wh}),d['id'])
        assert history[0]['difference']['changes'][0]['finance_preview']['original_payment_cash_ngn']=='1500.00'
        uid=operator_grants(db,wh,['packing.read','finance.read'])
    with TestClient(app) as client:
        client.post('/api/auth/login',json={'username':'operator','password':'synthetic-password-123'})
        assert client.get('/api/packing/documents/'+d['id']).status_code==200
        assert client.get('/api/packing/documents/'+d['id']+'/history').status_code==403
        with app.state.session_factory() as db,db.begin():db.add(WarehouseScope(user_id=uid,warehouse_id=other_wh))
        assert client.get('/api/packing/documents/'+d['id']+'/history').status_code==200
        with app.state.session_factory() as db,db.begin():db.execute(delete(Grant).where(Grant.user_id==uid,Grant.capability=='finance.read'))
        assert client.get('/api/packing/documents/'+d['id']+'/history').status_code==403


def test_i6_cross_warehouse_native_receipt_patch_http(app):
    from app.cargo import service as cargo
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        other,other_party=seed(db,actor);native=call(db,actor,cargo.create_receipt,**receipt_payload(other,other_party))
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        r=client.patch('/api/tickets/'+t['id'],json={'expected_version':1,'native_receipt_id':native['id']},headers={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'cross-native'})
        assert r.status_code==403,r.text
    with app.state.session_factory() as db:
        assert db.get(Ticket,t['id']).native_receipt_id is None


def test_i7_f6_explicit_exclusion_stays_out_of_operational_rows(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,_=seed(db,actor);batch=source_batch(db,actor,wh,'f6',True)
        rows=[r for r in batch['rows'] if r['eligible']]
        payload=dict(id=batch['id'],expected_version=1,selection=[{'row_id':rows[0]['id']}],excluded_rows=[{'row_id':rows[1]['id'],'reason':'Duplicate full/detail observation'}],idempotency_key='f6-disposition')
        committed=imports.commit_import(db,actor,payload)
        assert [r['id'] for r in imports.list_f6(db,actor)]==[rows[0]['id']]
        assert imports.commit_import(db,actor,payload)==committed
        retained=next(r for r in imports.get_preview(db,actor,batch['id'])['rows'] if r['id']==rows[1]['id'])
        assert retained['disposition']=='excluded' and retained['exclusion_reason']=='Duplicate full/detail observation'
        assert retained['raw']==rows[1]['raw']


def test_i8_replacement_can_reasonably_exclude_duplicate_observation(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=arrival(db,actor,t,3,'3','1');invoice_id=t['invoice_id']
        payment=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='1500',currency='NGN')
        call(db,actor,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':invoice_id,'expected_version':1,'amount_ngn':'1500'}])
        batch=source_batch(db,actor,wh,duplicate=True);rows=[r for r in batch['rows'] if r['eligible']]
        mapping=[{'row_id':rows[0]['id'],'ticket_id':t['id']}];excluded=[{'row_id':rows[1]['id'],'reason':'Repeated detail of selected FULL observation'}]
        preview=imports.replacement_preview(db,actor,d['id'],batch['id'],mapping,excluded_rows=excluded)
        with pytest.raises(DomainError) as stale:
            call(db,actor,imports.replace_document,id=d['id'],expected_version=preview['version'],batch_id=batch['id'],mapping=mapping,excluded_rows=[{'row_id':excluded[0]['row_id'],'reason':'Unreviewed different reason'}],preview=preview,reason='Invalid preview binding')
        assert stale.value.code=='preview_changed'
        d=call(db,actor,imports.replace_document,id=d['id'],expected_version=preview['version'],batch_id=batch['id'],mapping=mapping,excluded_rows=excluded,preview=preview,reason='Correct file observation mapping')
        assert len(d['tickets'])==1 and d['tickets'][0]['id']==t['id'] and d['tickets'][0]['invoice_id']==invoice_id
        assert finance.get_invoice(db,actor,invoice_id)['amount_ngn']=='1500.00'
        assert d['revisions'][-1]['difference']['excluded_rows']==excluded
        assert finance.get_payment(db,actor,payment['id'])['amount_ngn']=='1500.00'
        assert finance.get_payment(db,actor,payment['id'])['available_ngn']=='0.00'
        retained=imports.get_preview(db,actor,batch['id'])
        assert next(r for r in retained['rows'] if r['id']==rows[1]['id'])['raw']==rows[1]['raw']


def test_i3_ngn_settlement_prefix_cannot_bill_unarrived_source(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=1,pieces=10,kg='10',freight_usd='0.10',clearing_ngn='0',ngn_per_usd='1.5')
        for i in range(1,11):
            t=arrival(db,actor,t,cbm='0.1')
            due=Decimal(finance.get_invoice(db,actor,t['invoice_id'])['amount_ngn'])
            from decimal import ROUND_HALF_UP
            quota=(Decimal('.15')*i/10).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            assert due<=quota
        assert due==Decimal('.15')


def test_i2_mixed_pcs_freight_and_cbm_clearing_have_distinct_proofs(app):
    from app.models import CargoChargeComponent
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,_=make_document(db,actor,wh,party,'SEA')
        call(db,actor,pricing.create_card,transport='SEA',origin='GZ',goods_class='mixed',destination='TEST',unit='PCS',currency='NGN',unit_price='2000',clearing_per_cbm_ngn='100',valid_from='2026-01-01',valid_to='2026-12-31',source='Synthetic mixed unit tariff')
        t=call(db,actor,packing.create_ticket,document_id=d['id'],receipt='SYN2',party_id=party,pieces=3,kg='30',cbm='3',goods_class='mixed',destination='TEST',received_on='2026-11-01')
        original_components=t['charge_components'];t=arrival(db,actor,t,1,'1','0.5')
        assert t['arrivals'][0]['clearing_ngn']=='2050.00'
        proof=finance.get_invoice(db,actor,t['invoice_id'])['pricing']['arrival_allocation']
        assert {c['unit'] for c in proof['components']}=={'PCS','CBM'}
        assert {c['component_id'] for c in t['arrivals'][0]['charge_basis']}=={c['id'] for c in original_components}
        payment=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='2050',currency='NGN')
        call(db,actor,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':t['invoice_id'],'expected_version':1,'amount_ngn':'2050'}])
        t=arrival(db,actor,t,2,'29','2.5')
        assert finance.get_invoice(db,actor,t['invoice_id'])['amount_ngn']=='6300.00'
        assert finance.get_payment(db,actor,payment['id'])['amount_ngn']=='2050.00'
        assert [db.get(CargoChargeComponent,c['id']).unit for c in original_components]==['PCS','CBM']


def test_i2_legacy_totals_need_reviewed_basis_and_never_guess_unit(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,_=make_document(db,actor,wh,party)
        t=call(db,actor,packing.create_ticket,document_id=d['id'],receipt='TOTAL-ONLY',party_id=party,pieces=3,kg='30',cbm='3',freight_usd='0',clearing_ngn='6000')
        t=arrival(db,actor,t,1,'1','1')
        assert t['invoice_id'] is None and t['arrivals'][0]['clearing_ngn'] is None
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],charge_components=[{'component':'freight','currency':'NGN','unit':'PCS','amount':'6000','source':'Explicit review of old source tariff'}])
        assert finance.get_invoice(db,actor,t['invoice_id'])['amount_ngn']=='2000.00'
        assert t['arrivals'][0]['clearing_ngn'] is None and t['arrivals'][0]['effective_clearing_ngn']=='2000.00'


@pytest.mark.parametrize('usd,ngn',[('0.02','0'),('0','0.02'),('0.02','0.02')])
def test_i3_unequal_tiny_batches_conserve_every_prefix(app,usd,ngn):
    from decimal import ROUND_HALF_UP
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=1,pieces=4,kg='10',freight_usd=usd,clearing_ngn=ngn,ngn_per_usd='1.5')
        cumulative=Decimal(0)
        for kg in ('1','2','3','4'):
            cumulative+=Decimal(kg);t=arrival(db,actor,t,kg=kg,cbm=str(Decimal(kg)/10))
            for key,total in [('freight_usd',usd),('clearing_ngn',ngn)]:
                amounts=[Decimal(a[key]) for a in t['arrivals']]
                assert min(amounts)>=0 and sum(amounts)<=(Decimal(total)*cumulative/10).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        assert Decimal(finance.get_invoice(db,actor,t['invoice_id'])['amount_ngn'])==Decimal(usd)*Decimal('1.5')+Decimal(ngn)


def test_i6_native_link_old_and_new_scopes_replacement_and_replay(app):
    from app.cargo import service as cargo
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        native=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        payload=dict(id=t['id'],expected_version=1,native_receipt_id=native['id'],idempotency_key='native-link')
        linked=packing.update_ticket(db,actor,payload)
        revoked=Actor(actor.id,{'packing.read','packing.manage'},{wh})
        with pytest.raises(DomainError) as failure:packing.update_ticket(db,revoked,payload)
        assert failure.value.status==403
        with pytest.raises(DomainError) as failure:
            call(db,revoked,packing.update_ticket,id=t['id'],expected_version=2,native_receipt_id=None)
        assert failure.value.status==403
        other,oparty=seed(db,actor);remote=call(db,actor,cargo.create_receipt,**(receipt_payload(other,oparty,2)|{'series':'OTHER'}))
        batch=source_batch(db,actor,wh);row=next(r for r in batch['rows'] if r['eligible'])
        with pytest.raises(DomainError) as failure:
            imports.replacement_preview(db,actor,d['id'],batch['id'],[{'row_id':row['id'],'ticket_id':t['id'],'values':{'native_receipt_id':remote['id']}}])
        assert failure.value.status==403
        assert db.get(Ticket,t['id']).native_receipt_id==native['id']
        unlinked=call(db,actor,packing.update_ticket,id=t['id'],expected_version=linked['version'],native_receipt_id=None)
        assert unlinked['previous_native_receipt_id']==native['id']
        # A historical success cannot be replayed after the current link was removed.
        with pytest.raises(DomainError):packing.update_ticket(db,revoked,payload)


def test_i4_billed_preview_requires_finance_read_and_new_source_scope(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=arrival(db,actor,t,3,'3','1');other,_=seed(db,actor);batch=source_batch(db,actor,other)
        limited=Actor(actor.id,{'packing.read','packing.manage','packing.import','finance.adjust'},{wh})
        with pytest.raises(DomainError) as denied:packing.ticket_change_preview(db,limited,t['id'],{})
        assert denied.value.status==403
        with pytest.raises(DomainError) as denied:imports.replacement_preview(db,limited,d['id'],batch['id'],[])
        assert denied.value.status==403


def test_i6_import_native_reference_requires_authorized_matching_receipt(app):
    from app.cargo import service as cargo
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);other,oparty=seed(db,actor)
        native=call(db,actor,cargo.create_receipt,**receipt_payload(other,oparty))
        b=source_batch(db,actor,wh,'f6');row=next(r for r in b['rows'] if r['eligible'])
        with pytest.raises(DomainError) as denied:
            with db.begin_nested():
                call(db,actor,imports.commit_import,id=b['id'],expected_version=1,selection=[{'row_id':row['id'],'native_receipt_id':native['id']}])
        assert denied.value.status==403
        assert imports.get_preview(db,actor,b['id'])['status']=='preview'


@pytest.mark.parametrize('kind',['component','selection'])
def test_review_migration_preserves_component_and_exclusion_evidence(app,kind):
    from alembic import command
    from app.cli import migration_config
    from app.models import CargoChargeComponent,CargoSourceRow
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor)
        if kind=='component':
            # Quote-owned evidence reaches the c5 guard without a ticket triggering c6 first.
            from app.cargo import service as cargo
            receipt=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
            call(db,actor,pricing.quote_receipt,id=receipt['id'],expected_version=1,manual={'unit_price':'1','currency':'USD','unit':'KG'})
        else:
            b=source_batch(db,actor,wh,'f6',True);rows=[r for r in b['rows'] if r['eligible']]
            call(db,actor,imports.commit_import,id=b['id'],expected_version=1,selection=[{'row_id':rows[0]['id']}],excluded_rows=[{'row_id':rows[1]['id'],'reason':'Reviewed duplicate'}])
    config=migration_config();config.attributes['database_url']=app.state.settings.database_url
    with pytest.raises(RuntimeError,match='cannot be discarded'):command.downgrade(config,'c11000300004')
    with app.state.session_factory() as db:
        if kind=='component':assert len(list(db.scalars(select(CargoChargeComponent))))==2
        else:assert db.scalar(select(CargoSourceRow.exclusion_reason).where(CargoSourceRow.disposition=='excluded'))=='Reviewed duplicate'


def test_i2_historical_ar_unknown_basis_preview_reports_pending(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,_=make_document(db,actor,wh,party)
        t=call(db,actor,packing.create_ticket,document_id=d['id'],receipt='OLD-TOTAL',party_id=party,pieces=3,kg='30',cbm='3',freight_usd='0',clearing_ngn='6000')
        inv=call(db,actor,finance.create_invoice,warehouse_id=wh,party_id=party,reference='Synthetic legacy invoice',amount_ngn='6000',uploaded_on='2026-01-01')
        db.get(Ticket,t['id']).invoice_id=inv['id'];db.flush();t=arrival(db,actor,t,1,'1','1')
        with pytest.raises(DomainError,match='priced arrivals'):packing.ticket_change_preview(db,actor,t['id'],{})
        assert finance.get_invoice(db,actor,inv['id'])['amount_ngn']=='6000.00'


def test_i2_pre_component_allocation_proof_does_not_rebill_legacy_prefix(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,_=make_document(db,actor,wh,party)
        t=call(db,actor,packing.create_ticket,document_id=d['id'],receipt='OLD-PARTIAL',party_id=party,pieces=3,kg='30',cbm='3',freight_usd='0',clearing_ngn='6000')
        inv=call(db,actor,finance.create_invoice,warehouse_id=wh,party_id=party,reference='Pre-component prefix AR',amount_ngn='2000',uploaded_on='2026-01-01')
        db.get(Ticket,t['id']).invoice_id=inv['id']
        db.get(Invoice,inv['id']).pricing={'arrival_allocation':{'amount_ngn':'2000.00','rounding_adjustment_ngn':'0.00'}}
        db.flush();t=arrival(db,actor,t,1,'1','1')
        assert finance.get_invoice(db,actor,inv['id'])['amount_ngn']=='2000.00'
        assert t['arrivals'][0]['clearing_ngn'] is None


def test_i2_legacy_basis_resolution_requires_financial_preview_even_same_amount(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,_=make_document(db,actor,wh,party)
        t=call(db,actor,packing.create_ticket,document_id=d['id'],receipt='OLD-RESOLVE',party_id=party,pieces=3,kg='30',cbm='3',freight_usd='0',clearing_ngn='6000')
        inv=call(db,actor,finance.create_invoice,warehouse_id=wh,party_id=party,reference='Synthetic partial source invoice',amount_ngn='2000',charge_usd='0',charge_ngn='2000',uploaded_on='2026-01-01')
        db.get(Ticket,t['id']).invoice_id=inv['id'];db.get(Invoice,inv['id']).pricing={'arrival_allocation':{'amount_ngn':'2000.00','rounding_adjustment_ngn':'0.00'}};db.flush()
        payment=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='1000',currency='NGN')
        call(db,actor,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':inv['id'],'expected_version':1,'amount_ngn':'1000'}])
        t=arrival(db,actor,t,1,'1','1')
        changes={'charge_components':[{'component':'freight','currency':'NGN','unit':'PCS','amount':'6000','source':'Reviewed historical PCS tariff'}]}
        with pytest.raises(DomainError) as missing:
            with db.begin_nested():call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],reason='Review prior basis',**changes)
        assert missing.value.code=='preview_changed'
        preview=packing.ticket_change_preview(db,actor,t['id'],changes)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],finance_preview=preview['finance_preview'],reason='Review prior basis',**changes)
        resolved=finance.get_invoice(db,actor,inv['id'])
        assert resolved['amount_ngn']=='2000.00' and resolved['version']==3
        assert resolved['pricing']['arrival_allocation']['components'][0]['unit']=='PCS'
        assert finance.get_payment(db,actor,payment['id'])['amount_ngn']=='1000.00'
        assert finance.get_payment(db,actor,payment['id'])['available_ngn']=='0.00'
        t=arrival(db,actor,t,1,'1','1')
        assert t['invoice_id']==inv['id'] and finance.get_invoice(db,actor,inv['id'])['amount_ngn']=='4000.00'
