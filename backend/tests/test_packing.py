from io import BytesIO
from decimal import Decimal
import pytest
from openpyxl import Workbook,load_workbook
from sqlalchemy import select
from fastapi.testclient import TestClient
from app.attachments import put_file
from app.common import DomainError
from app.models import Invoice,Payment
from app.finance import service as finance
from test_logistics import who,call,seed


def workbook():
    w=Workbook();s=w.active;s.title='P.H'
    s.append(['RECEIPT NO.','MARK','DESCRIPTION','PCS','CBM','GROSS KG','NET KG','CONTACT NUMBER','PER CBM CHARGES USD','CLEARING CHARGE PER CBM NGN'])
    s.append(['SYN1','Mark A','Item A',2,1.25,10,8,'ON HOLD BY +2347000000000',20,1000])
    s.append([None,'Mark A','Item B',1,.5,5,3,None,20,1000]);s.merge_cells('A2:A3')
    s.append(['','TOTAL',3,3,1.75,15])
    s.append(['Notes','This is not cargo',None,99])
    x=w.create_sheet('ABA');x.append(['收据号','客户','品名','件数','体积','毛重','目的地(MBL)','库房名称','客户电话','业务员'])
    x.append(['SYN1','Mark A','Item C',1,1,9,'ABA','Storage X',None,'Alice'])
    b=BytesIO();w.save(b);return b.getvalue()


def upload(db,actor,content=None):
    return put_file(db,actor,content or workbook(),'synthetic.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','user',actor.id).id


def test_preview_http_preserves_merged_rows_total_terminator_and_gross(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,_=seed(db,actor);attachment=upload(db,actor)
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        r=client.post('/api/cargo/imports/preview',json={'warehouse_id':wh,'attachment_id':attachment,'kind':'packing'},headers={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'preview'})
        assert r.status_code==200,r.text
        rows=[row for row in r.json()['rows'] if row['eligible']]
        assert len(rows)==3 and [r['receipt'] for r in rows]==['SYN1']*3
        assert rows[1]['provenance']['receipt']['merged_range']=='A2:A3'
        assert rows[0]['kg']=='10.000000' and rows[0]['freight_usd']=='25.00' and rows[0]['clearing_ngn']=='1250.00'
        assert rows[0]['destination']=='PORT HARCOURT'
        assert rows[1]['contact_raw'] is None and rows[2]['contact_raw'] is None


def make_document(db,actor,wh,party,transport='AIR'):
    from app.shipments import service as shipping
    from app.packing import service as packing
    s=call(db,actor,shipping.create_shipment,warehouse_id=wh,reference='Synthetic',transport=transport,origin='GZ')
    d=call(db,actor,packing.create_document,shipment_id=s['id'],reference='Synthetic document',uploaded_on='2026-11-01')
    t=call(db,actor,packing.create_ticket,document_id=d['id'],receipt='SYN1',party_id=party,transport=transport,origin='GZ',pieces=3,kg='3',cbm='1',freight_usd='1',clearing_ngn='0',ngn_per_usd='1500')
    return d,t


def test_partial_air_arrivals_exact_last_remainder_one_ar(app):
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        assert not list(db.scalars(select(Invoice)))
        a=call(db,actor,packing.arrive,id=t['id'],expected_version=1,pieces=1,kg='1',cbm='0.333333',occurred_on='2026-11-04',source='Manual arrival',mode='air')
        assert a['arrivals'][0]['freight_usd']=='0.33' and finance.get_invoice(db,actor,a['invoice_id'])['amount_ngn']=='495.00'
        b=call(db,actor,packing.arrive,id=t['id'],expected_version=2,pieces=2,kg='2',cbm='0.666667',occurred_on='2026-11-05',source='Final batch',mode='air')
        assert b['arrivals'][1]['freight_usd']=='0.67'
        assert b['invoice_id']==a['invoice_id'] and len(list(db.scalars(select(Invoice))))==1
        assert finance.get_invoice(db,actor,b['invoice_id'])['amount_ngn']=='1500.00'


def test_paid_document_split_and_void_preserve_cash_and_attachments(app):
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        a=call(db,actor,packing.arrive,id=t['id'],expected_version=1,pieces=3,kg='3',cbm='1',occurred_on='2026-11-04',source='Synthetic arrival',mode='air')
        payment=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='1500',currency='NGN')
        call(db,actor,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':a['invoice_id'],'expected_version':1,'amount_ngn':'1500'}])
        attachment=upload(db,actor)
        a=call(db,actor,packing.attach_ticket,id=t['id'],expected_version=2,attachment_id=attachment)
        current=packing.get_document(db,actor,d['id'])
        split=call(db,actor,packing.split_document,id=d['id'],expected_version=current['version'],ticket_ids=[t['id']],reference='Synthetic split')
        moved=packing.get_ticket(db,actor,t['id'])
        assert moved['invoice_id']==a['invoice_id'] and moved['attachments'][0]['attachment_id']==attachment
        assert moved['document_id']==split['new_document']['id']
        preview=packing.void_preview(db,actor,split['new_document']['id'])
        voided=call(db,actor,packing.void_document,id=split['new_document']['id'],expected_version=split['new_document']['version'],preview=preview,reason='Explicit synthetic cancellation')
        assert voided['state']=='void'
        assert finance.get_invoice(db,actor,a['invoice_id'])['state']=='void'
        assert finance.get_payment(db,actor,payment['id'])['amount_ngn']=='1500.00'
        assert finance.get_payment(db,actor,payment['id'])['available_ngn']=='1500.00'
        assert packing.get_ticket(db,actor,t['id'])['arrivals'][0]['kg']=='3.000000'
        restoration=packing.recover_preview(db,actor,voided['id'])
        restored=call(db,actor,packing.recover_document,id=voided['id'],expected_version=voided['version'],reason='Reviewed synthetic recovery',preview=restoration)
        assert restored['state']=='active'
        restored_invoice=finance.get_invoice(db,actor,a['invoice_id'])
        assert restored_invoice['amount_ngn']=='1500.00' and restored_invoice['outstanding_ngn']=='1500.00'
        assert finance.get_payment(db,actor,payment['id'])['available_ngn']=='1500.00'


def test_three_batches_round_only_each_batch_then_last_remainder(app):
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        for version,cbm,expected in [(1,'0.333333','495.00'),(2,'0.333333','990.00'),(3,'0.333334','1500.00')]:
            result=call(db,actor,packing.arrive,id=t['id'],expected_version=version,pieces=1,kg='1',cbm=cbm,occurred_on='2026-11-04',source='Synthetic batch',mode='air')
            assert finance.get_invoice(db,actor,result['invoice_id'])['amount_ngn']==expected
        assert [a['freight_usd'] for a in result['arrivals']]==['0.33','0.33','0.34']


def test_replacement_reorder_keeps_paid_identity_and_readonly_originals(app):
    from app.packing import service as packing,imports
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        arrived=call(db,actor,packing.arrive,id=t['id'],expected_version=1,pieces=3,kg='3',cbm='1',occurred_on='2026-11-04',source='Arrival',mode='air')
        pay=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='1500',currency='NGN')
        call(db,actor,finance.post_payment,id=pay['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':arrived['invoice_id'],'expected_version':1,'amount_ngn':'1500'}])
        b=Workbook();s=b.active;s.append(['RECEIPT NO.','MARK','DESCRIPTION','PCS','CBM','KG','FREIGHT USD','CLEARING NGN']);s.append(['SYN1','Changed mark','Revised',3,1,3,.8,0]);stream=BytesIO();b.save(stream)
        preview=call(db,actor,imports.preview_import,warehouse_id=wh,attachment_id=upload(db,actor,stream.getvalue()),kind='packing')
        source=next(r for r in preview['rows'] if r['eligible'])
        mapping=[{'row_id':source['id'],'ticket_id':t['id']}]
        difference=imports.replacement_preview(db,actor,d['id'],preview['id'],mapping)
        changed=call(db,actor,imports.replace_document,id=d['id'],expected_version=difference['version'],batch_id=preview['id'],mapping=mapping,preview=difference,reason='Reviewed corrected source')
        row=changed['tickets'][0]
        assert row['id']==t['id'] and row['invoice_id']==arrived['invoice_id']
        assert row['arrivals'][0]['kg']=='3.000000'
        assert finance.get_payment(db,actor,pay['id'])['amount_ngn']=='1500.00'
        assert finance.get_payment(db,actor,pay['id'])['available_ngn']=='300.00'
        assert finance.get_invoice(db,actor,row['invoice_id'])['due_on']=='2026-11-15'


def test_manual_pcs_ticket_reprice_updates_paid_ar_and_sea_air_mode_rejected(app):
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=1,manual={'unit_price':'2000','unit':'PCS','currency':'NGN'},fx={})
        assert t['clearing_ngn']=='6000.00' and t['quote']['amount_ngn']=='6000.00'
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=2,destination='ABA')
        assert t['quote']['unit']=='PCS' and t['clearing_ngn']=='6000.00'
        a=call(db,actor,packing.arrive,id=t['id'],expected_version=3,pieces=3,kg='3',cbm='1',occurred_on='2026-11-04',source='Synthetic',mode='air')
        assert finance.get_invoice(db,actor,a['invoice_id'])['amount_ngn']=='6000.00'
        sea,tsea=make_document(db,actor,wh,party,'SEA')
        with pytest.raises(DomainError,match='match transport'):
            call(db,actor,packing.arrive,id=tsea['id'],expected_version=1,pieces=3,kg='3',cbm='1',occurred_on='2026-11-04',source='Synthetic',mode='air')


def test_original_header_shapes_sparse_formatting_and_ambiguous_total_preserved():
    from app.packing.parser import parse
    from openpyxl.styles import Font
    book=Workbook();sheet=book.active
    sheet.append(['RECEIPT NO.','MARK','DESCRIPTION','QTY',None,'MEAS.(CBM)',None,'PER CBM CHARGES','CLEARING CHARGE\nPER CBM                  TOTAL','CONTACT NUMBER'])
    sheet.append(['SYN-A','Mark','Item','3 CTNS',None,2,None,50,120000,None])
    sheet.cell(1,16384).font=Font(bold=True)
    output=BytesIO();book.save(output)
    rows,_,_=parse(output.getvalue());cargo=[r for r in rows if r['eligible']]
    assert len(cargo)==1 and cargo[0]['cbm']==Decimal('2.000000')
    assert cargo[0]['freight_usd']==Decimal('100.00')
    assert cargo[0]['clearing_ngn'] is None and 'clearing_ngn_basis_unconfirmed' in cargo[0]['issues']
    assert 'pieces_invalid' in cargo[0]['issues'] and cargo[0]['raw'][3]=='3 CTNS'


def test_same_document_group_uses_confirmed_phone_or_exact_mark_and_keeps_conflicts(app):
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        call(db,actor,packing.update_ticket,id=t['id'],expected_version=1,mark='A',contact_raw='+2347000000000',phone_normalized='+2347000000000',phone_confirmed=True)
        second=call(db,actor,packing.create_ticket,document_id=d['id'],receipt='SYN2',mark='B',contact_raw='+2347000000000',phone_normalized='+2347000000000',phone_confirmed=True,party_id=party,pieces=1,kg='1',cbm='1',destination='ABA')
        groups=packing.document_groups(db,actor,d['id'])
        assert len(groups)==1 and set(groups[0]['ticket_ids'])=={t['id'],second['id']}


@pytest.mark.parametrize('usd,ngn,want', [('1','0',['0.50','1.00','1.50']),('0','1',['0.33','0.66','1.00']),('1','1',['0.83','1.66','2.50'])])
def test_arrival_settlement_rounding_is_explicit_and_control_total_conserved(app,usd,ngn,want):
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=1,freight_usd=usd,clearing_ngn=ngn,ngn_per_usd='1.5')
        for index,cbm in enumerate(['0.333333','0.333333','0.333334']):
            t=call(db,actor,packing.arrive,id=t['id'],expected_version=index+2,pieces=1,kg='1',cbm=cbm,occurred_on='2026-11-04',source='Synthetic rounding batch',mode='air')
            inv=finance.get_invoice(db,actor,t['invoice_id']);assert inv['amount_ngn']==want[index]
            assert 'rounding_adjustment_ngn' in inv
        assert inv['rounding_adjustment_ngn']=='0.00'


def test_rounding_paid_before_final_and_unfounded_adjustment_rejected(app):
    from app.packing import service as packing
    from app.finance.rounding import cargo_basis
    from app.models import Ticket,ArrivalSettlement
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=1,ngn_per_usd='1.5')
        for index in range(2):t=call(db,actor,packing.arrive,id=t['id'],expected_version=index+2,pieces=1,kg='1',cbm='0.333333',occurred_on='2026-11-04',source='Synthetic',mode='air')
        inv=finance.get_invoice(db,actor,t['invoice_id']);assert inv['amount_ngn']=='1.00' and inv['rounding_adjustment_ngn']=='0.01'
        pay=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='1',currency='NGN')
        call(db,actor,finance.post_payment,id=pay['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':inv['id'],'expected_version':inv['version'],'amount_ngn':'1'}])
        from app.finance.export import export_approved
        from app.attachments import read_file
        from app.models import Attachment
        db.info['settings']=app.state.settings
        export=call(db,actor,export_approved,warehouse_id=wh,on=finance.totals(db,actor,wh)['day'])
        book=load_workbook(BytesIO(read_file(app.state.settings,db.get(Attachment,export['id']))),data_only=True)
        assert book['Invoice components'].cell(2,6).value==0.01
        t=call(db,actor,packing.arrive,id=t['id'],expected_version=4,pieces=1,kg='1',cbm='0.333334',occurred_on='2026-11-04',source='Last synthetic batch',mode='air')
        inv=finance.get_invoice(db,actor,t['invoice_id']);assert inv['amount_ngn']=='1.50' and inv['outstanding_ngn']=='0.50'
        assert finance.get_payment(db,actor,pay['id'])['amount_ngn']=='1.00'
        settlements=list(db.scalars(select(ArrivalSettlement).where(ArrivalSettlement.invoice_id==inv['id'],ArrivalSettlement.invoice_version==inv['version']).order_by(ArrivalSettlement.created_at)))
        assert sum(s.settled_ngn for s in settlements)==Decimal('1.50')
        basis=cargo_basis(db,db.get(Ticket,t['id']));basis['rounding_adjustment_ngn']='999'
        with pytest.raises(DomainError,match='basis changed'):
            finance.adjustment_preview(db,actor,inv['id'],'1000',rounding_basis=basis)
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        response=client.post('/api/invoices/'+inv['id']+'/adjust',json={'expected_version':inv['version'],'amount_ngn':'1000','reason':'Forged rounding','preview':{},'rounding_adjustment_ngn':'999'},headers={'X-CSRF-Token':login['csrf_token'],'Idempotency-Key':'forged-rounding'})
        assert response.status_code==422


def test_unknown_source_quantities_are_not_replaced_with_zero():
    from app.packing.service import ticket_values
    result=ticket_values({'receipt':None,'pieces':None,'kg':None,'cbm':None})
    assert result=={'receipt':None,'pieces':None,'kg':None,'cbm':None}


def test_import_can_commit_missing_gross_weight_as_pending_without_stock(app):
    from app.packing import imports,service as packing
    from app.shipments import service as shipments
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;actor=who(db);wh,party=seed(db,actor)
        w=Workbook();w.active.append(['RECEIPT','DESCRIPTION','PCS','CBM']);w.active.append(['SYN-X','Original item',3,2]);b=BytesIO();w.save(b)
        preview=call(db,actor,imports.preview_import,warehouse_id=wh,attachment_id=upload(db,actor,b.getvalue()),kind='packing')
        s=call(db,actor,shipments.create_shipment,warehouse_id=wh,reference='Synthetic missing weight',transport='SEA')
        d=call(db,actor,imports.commit_import,id=preview['id'],expected_version=1,selection=[{'row_id':r['id']} for r in preview['rows'] if r['eligible']],shipment_id=s['id'],reference='Pending source',uploaded_on='2026-11-04')
        t=d['tickets'][0];assert t['kg'] is None and t['party_id'] is None and t['invoice_id'] is None
        with pytest.raises(DomainError,match='Confirm original'):
            call(db,actor,packing.arrive,id=t['id'],expected_version=1,pieces=3,kg='0',cbm='2',occurred_on='2026-11-04',source='Cannot invent missing weight',mode='sea')


def test_existing_historical_ar_is_not_shrunk_by_new_partial_arrival_rule(app):
    from app.packing import service as packing
    from app.models import Ticket
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        historical=call(db,actor,finance.create_invoice,warehouse_id=wh,party_id=party,reference='Historical source AR',amount_ngn='1500',uploaded_on='2026-01-01')
        db.get(Ticket,t['id']).invoice_id=historical['id']
        db.get(Ticket,t['id']).clearing_ngn=None  # Historic total is known; component decomposition is not.
        db.flush()
        t=call(db,actor,packing.arrive,id=t['id'],expected_version=1,pieces=1,kg='1',cbm='0.333333',occurred_on='2026-11-04',source='New actual arrival observation',mode='air')
        invoice=finance.get_invoice(db,actor,historical['id'])
        assert invoice['amount_ngn']=='1500.00' and invoice['due_on']=='2026-01-15'


def test_individual_ticket_void_credits_paid_cash_without_voiding_other_rows(app):
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.arrive,id=t['id'],expected_version=1,pieces=3,kg='3',cbm='1',occurred_on='2026-11-04',source='Synthetic arrival',mode='air')
        second=call(db,actor,packing.create_ticket,document_id=d['id'],receipt='SYN1',party_id=party,pieces=2,kg='2',cbm='1')
        payment=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='1500',currency='NGN')
        call(db,actor,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':t['invoice_id'],'expected_version':1,'amount_ngn':'1500'}])
    with TestClient(app) as client:
        auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        preview=client.get('/api/tickets/'+t['id']+'/void-preview')
        assert preview.status_code==200,preview.text
        result=client.post('/api/tickets/'+t['id']+'/void',json={'expected_version':2,'preview':preview.json(),'reason':'Explicit removal from corrected source'},headers={'X-CSRF-Token':auth['csrf_token'],'Idempotency-Key':'single-void'})
        assert result.status_code==200,result.text
        assert result.json()['state']=='void'
    with app.state.session_factory() as db:
        assert packing.get_ticket(db,actor,second['id'])['state']=='active'
        assert packing.get_document(db,actor,d['id'])['state']=='active'
        assert finance.get_payment(db,actor,payment['id'])['amount_ngn']=='1500.00'
        assert finance.get_payment(db,actor,payment['id'])['available_ngn']=='1500.00'


def test_unknown_clearing_component_does_not_become_zero_ar(app):
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,t=make_document(db,actor,wh,party,'SEA')
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=1,clearing_ngn=None)
        arrived=call(db,actor,packing.arrive,id=t['id'],expected_version=2,pieces=3,kg='3',cbm='1',occurred_on='2026-11-04',source='Actual arrival with unresolved original clearing column',mode='sea')
        assert arrived['clearing_ngn'] is None and arrived['invoice_id'] is None
        resolved=call(db,actor,packing.update_ticket,id=t['id'],expected_version=3,clearing_ngn='0')
        assert finance.get_invoice(db,actor,resolved['invoice_id'])['amount_ngn']=='1500.00'
