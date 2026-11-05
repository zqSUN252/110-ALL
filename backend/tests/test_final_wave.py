"""Whole-branch review regressions; isolated synthetic PG facts only."""
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common import DomainError
from app.finance import service as finance
from app.packing import service as packing
from app.cargo import service as cargo
from app.models import Invoice, Ticket, HoldFact, Shipment
from test_logistics import who, call, seed, receipt_payload
from test_packing import make_document


def arrive(db, actor, ticket):
    return call(db, actor, packing.arrive, id=ticket['id'], expected_version=ticket['version'],
                pieces=ticket['pieces'], kg=ticket['kg'], cbm=ticket['cbm'],
                occurred_on='2026-11-04', source='Synthetic actual arrival', mode='air')


def test_final_i01_recovered_native_invoice_accepts_explicit_reallocation(app):
    with app.state.session_factory() as db, db.begin():
        actor=who(db); wh,party=seed(db,actor); doc,t=make_document(db,actor,wh,party)
        t=arrive(db,actor,t)
        pay=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='1500',currency='NGN')
        call(db,actor,finance.post_payment,id=pay['id'],expected_version=pay['version'],approval='manual',
             allocations=[dict(invoice_id=t['invoice_id'],expected_version=1,amount_ngn='1500')])
        v=packing.void_preview(db,actor,doc['id'])
        call(db,actor,packing.void_document,id=doc['id'],expected_version=v['version'],preview=v,reason='Synthetic cancellation')
        v=packing.recover_preview(db,actor,doc['id'])
        call(db,actor,packing.recover_document,id=doc['id'],expected_version=v['version'],preview=v,reason='Synthetic recovery')
        inv=finance.get_invoice(db,actor,t['invoice_id']); current=finance.get_payment(db,actor,pay['id'])
        assert inv['outstanding_ngn']=='1500.00' and current['available_ngn']=='1500.00'
        # A fresh explicit allocation through the real authorized command must be
        # possible; recovery itself never does it.
        call(db,actor,finance.post_payment,id=pay['id'],expected_version=current['version'],approval='manual',
             allocations=[dict(invoice_id=inv['id'],expected_version=inv['version'],amount_ngn='1500')])
        assert finance.get_invoice(db,actor,inv['id'])['outstanding_ngn']=='0.00'


def test_final_i02_document_recovery_keeps_prior_individual_void_excluded(app):
    from app.packing import imports
    from test_packing import upload
    from openpyxl import Workbook
    from io import BytesIO
    with app.state.session_factory() as db, db.begin():
        db.info['settings']=app.state.settings
        actor=who(db); wh,party=seed(db,actor); doc,a=make_document(db,actor,wh,party)
        a=arrive(db,actor,a)
        b=call(db,actor,packing.create_ticket,document_id=doc['id'],receipt='SYN-B',party_id=party,pieces=1,kg='1',cbm='1')
        preview=packing.ticket_void_preview(db,actor,a['id'])
        a=call(db,actor,packing.void_ticket,id=a['id'],expected_version=a['version'],preview=preview,reason='Remove old row from replacement')
        book=Workbook();book.active.append(['RECEIPT NO.','PCS','KG','CBM']);book.active.append(['SYN-B',1,1,1]);stream=BytesIO();book.save(stream)
        batch=call(db,actor,imports.preview_import,warehouse_id=wh,attachment_id=upload(db,actor,stream.getvalue()),kind='packing')
        mapping=[dict(row_id=next(r['id'] for r in batch['rows'] if r['eligible']),ticket_id=b['id'])]
        pv=imports.replacement_preview(db,actor,doc['id'],batch['id'],mapping)
        call(db,actor,imports.replace_document,id=doc['id'],expected_version=pv['version'],batch_id=batch['id'],mapping=mapping,preview=pv,reason='Only current member')
        original_a=packing.get_ticket(db,actor,a['id'])
        for _ in range(2):
            pv=packing.void_preview(db,actor,doc['id'])
            call(db,actor,packing.void_document,id=doc['id'],expected_version=pv['version'],preview=pv,reason='Current document cancelled')
            pv=packing.recover_preview(db,actor,doc['id'])
            call(db,actor,packing.recover_document,id=doc['id'],expected_version=pv['version'],preview=pv,reason='Current document restored')
            assert packing.get_ticket(db,actor,a['id'])==original_a
            assert finance.get_invoice(db,actor,a['invoice_id'])['state']=='void'
            assert packing.get_ticket(db,actor,b['id'])['state']=='active'


def test_final_i03_multistep_merge_retains_original_hold_and_revoke(app):
    from app.documents import release
    with app.state.session_factory() as db, db.begin():
        actor=who(db); wh,party=seed(db,actor)
        source=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,1,customer_phone='ON HOLD BY SUPPLIER'))
        middle=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,2))
        target=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,3))
        middle=call(db,actor,cargo.merge_receipts,id=middle['id'],expected_version=middle['version'],sources=[dict(receipt_id=source['id'],expected_version=source['version'])],occurred_on='2026-11-04',reason='Synthetic transfer')
        target=call(db,actor,cargo.merge_receipts,id=target['id'],expected_version=target['version'],sources=[dict(receipt_id=middle['id'],expected_version=middle['version'])],occurred_on='2026-11-04',reason='Synthetic second transfer')
        from app.models import Receipt
        held=release.cargo_hold_state(db,db.get(Receipt,target['id']))
        assert held['active'] and len(held['facts'])==1
        fact=db.scalar(select(HoldFact).where(HoldFact.receipt_id==source['id']))
        current=cargo.get_receipt(db,actor,source['id'])
        call(db,actor,release.remove_holds,members=[dict(hold_id=fact.id,expected_version=fact.version,source_version=current['version'])],reason='Supplier evidence',removed_on='2026-11-04',confirmed_phone='+2347000000000',ngn_per_usd='1500')
        assert not release.cargo_hold_state(db,db.get(Receipt,target['id']))['active']
        call(db,actor,release.cancel_removal,id=fact.id,expected_version=fact.version,reason='Supervisor withdraws evidence')
        assert release.cargo_hold_state(db,db.get(Receipt,target['id']))['active']


def test_final_new_i01_merge_rejects_void_ticket_source_reference(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
        source=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,1))
        target=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,2))
        doc,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],
               native_receipt_id=source['id'],receipt=source['reference'])
        preview=packing.void_preview(db,actor,doc['id'])
        call(db,actor,packing.void_document,id=doc['id'],expected_version=preview['version'],preview=preview,reason='Synthetic cancellation')
        with pytest.raises(DomainError,match='source|Source'):
            call(db,actor,cargo.merge_receipts,id=target['id'],expected_version=target['version'],
                 sources=[dict(receipt_id=source['id'],expected_version=source['version'])],
                 occurred_on='2026-11-04',reason='Synthetic merge attempt')


def test_final_new_i01_recovery_preview_rejects_merged_native_source(app):
    from app.models import Receipt
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
        source=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party,1))
        doc,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],
               native_receipt_id=source['id'],receipt=source['reference'])
        preview=packing.void_preview(db,actor,doc['id'])
        call(db,actor,packing.void_document,id=doc['id'],expected_version=preview['version'],preview=preview,reason='Synthetic cancellation')
        native=db.get(Receipt,source['id']);native.state='merged';native.version+=1;db.flush()
        with pytest.raises(DomainError,match='merged'):
            packing.recover_preview(db,actor,doc['id'])


def test_final_i09_clearing_ap_preview_is_idempotent_and_stale_rejected(app):
    from app.shipments import service as shipping
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor)
        shipment=call(db,actor,shipping.create_shipment,warehouse_id=wh,reference='Synthetic clearing AP',transport='SEA',clearing_on='2026-11-04',clearing_party_id=party,clearing_amount_ngn='1000')
        invoice=finance.get_invoice(db,actor,shipment['clearing_invoice_id'])
        preview=finance.adjustment_preview(db,actor,invoice['id'],'1200')
        payload=dict(id=shipment['id'],expected_version=shipment['version'],clearing_amount_ngn='1200',finance_preview=preview,idempotency_key='synthetic-clearing-idempotent')
        first=shipping.update_shipment(db,actor,dict(payload))
        again=shipping.update_shipment(db,actor,dict(payload))
        assert again==first
        assert finance.get_invoice(db,actor,invoice['id'])['amount_ngn']=='1200.00'
        current=db.get(Shipment,shipment['id'])
        with pytest.raises(DomainError,match='preview_changed|Financial impact changed'):
            shipping.update_shipment(db,actor,dict(id=shipment['id'],expected_version=current.version,clearing_amount_ngn='1300',finance_preview=preview,idempotency_key='synthetic-clearing-stale'))
        assert finance.get_invoice(db,actor,invoice['id'])['amount_ngn']=='1200.00'


def test_final_i03_active_hold_blocks_release_order(app):
    from app.tracking import service as tracking
    from app.documents import release
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);doc,t=make_document(db,actor,wh,party)
        t=call(db,actor,packing.update_ticket,id=t['id'],expected_version=t['version'],contact_raw='ON HOLD BY SUPPLIER',phone_normalized=None,phone_confirmed=False,destination_warehouse_id=wh,address='Synthetic address')
        t=arrive(db,actor,t)
        call(db,actor,tracking.set_node,target_type='ticket',target_id=t['id'],expected_version=t['version'],node='warehouse_arrived',mode='manual',destination_warehouse_id=wh,occurred_at='2026-11-04T10:00:00+08:00',source='Synthetic warehouse receipt')
        t=packing.get_ticket(db,actor,t['id'])
        with pytest.raises(DomainError,match='manufacturer'):
            call(db,actor,release.issue_order,members=[dict(ticket_id=t['id'],expected_version=t['version'])],mode='paid')


def test_final_i04_linked_tickets_share_source_quantity_ceiling(app):
    with app.state.session_factory() as db, db.begin():
        actor=who(db); wh,party=seed(db,actor)
        p=receipt_payload(wh,party);p['specs']=[dict(length='1',width='1',height='1',unit='M',pieces=10,kg='10')]
        r=call(db,actor,cargo.create_receipt,**p);doc,_=make_document(db,actor,wh,party)
        fields=dict(document_id=doc['id'],receipt=r['reference'],native_receipt_id=r['id'],party_id=party,pieces=10,kg='10',cbm='10')
        call(db,actor,packing.create_ticket,**fields)
        with pytest.raises(DomainError,match='source|Source'):
            call(db,actor,packing.create_ticket,**fields)


def test_final_i05_confirmed_same_customer_or_group_creates_one_real_invoice(app):
    with app.state.session_factory() as db, db.begin():
        actor=who(db); wh,party=seed(db,actor);doc,a=make_document(db,actor,wh,party)
        a=call(db,actor,packing.update_ticket,id=a['id'],expected_version=a['version'],mark='Same synthetic Mark')
        b=call(db,actor,packing.create_ticket,document_id=doc['id'],receipt='SYN-B',party_id=party,mark=a['mark'],pieces=3,kg='3',cbm='1',
               freight_usd='1',clearing_ngn='0',ngn_per_usd='1500',charge_components=[dict(component='freight',currency='USD',unit='KG',amount='1',source='Explicit synthetic charge'),dict(component='clearing',currency='NGN',unit='CBM',amount='0',source='No clearing')])
        a=arrive(db,actor,a);b=arrive(db,actor,b)
        assert a['invoice_id']==b['invoice_id']
        assert len(list(db.scalars(select(Invoice))))==1
        assert finance.get_invoice(db,actor,a['invoice_id'])['amount_ngn']=='3000.00'
