import json
from decimal import Decimal
from sqlalchemy import select
from app.models import Invoice, HoldFact, Payment
from app.finance import service as finance
from app.packing import service as packing
from app.documents import release, notifications
from app.tracking import service as tracking
from app.migration import service as migration
from test_migration import sample_source
from test_migration_review import sourced_group
from test_logistics import call


def test_final_i06_shared_legacy_notice_counts_invoice_and_payment_once(app):
    source=sample_source();ships=json.loads(source['data']['logistics_shipments'])
    for t,amount in zip(ships[0]['tickets'],(400,600)):t.update(freightUSD=0,clearingNGN=amount)
    source['data']['logistics_shipments']=json.dumps(ships)
    bills=json.loads(source['data']['logistics_bills']);bills[0].update(amount=1000,received=200,discount=0)
    source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        actor,wh,party,bill,tickets=sourced_group(app,db,source)
        invoice=db.get(Invoice,bill.native_id)
        pay=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='300',currency='NGN',paid_on='2026-01-03')
        call(db,actor,finance.post_payment,id=pay['id'],expected_version=1,approval='manual',allocations=[dict(invoice_id=invoice.id,expected_version=invoice.version,amount_ngn='300')])
        for t in tickets:
            migration.resolve_charge_basis(db,actor,t.id,dict(expected_version=t.version,source='Explicit original CBM',charge_components=[dict(component='clearing',currency='NGN',unit='CBM',amount=str(t.clearing_ngn),source='Original charge')],idempotency_key='basis-'+t.id))
            t.destination_warehouse_id=wh
            call(db,actor,packing.arrive,id=t.id,expected_version=t.version,pieces=t.pieces,kg=None,cbm=str(t.cbm),occurred_on='2026-01-02',source='Actual synthetic arrival',mode='sea')
            call(db,actor,tracking.set_node,target_type='ticket',target_id=t.id,expected_version=t.version,node='warehouse_arrived',mode='manual',destination_warehouse_id=wh,occurred_at='2026-01-03T10:00:00+08:00',source='Actual synthetic warehouse confirmation')
        order=call(db,actor,release.issue_order,members=[dict(ticket_id=t.id,expected_version=t.version) for t in tickets],mode='credit',promise_on='2026-12-01',guarantor='Synthetic guarantor')
        notice=call(db,actor,notifications.render,kind='release',ticket_ids=[t.id for t in tickets],order_id=order['id'])
        assert 'Full Original Invoice Payable Amount: NGN 1000.00' in notice['text']
        assert 'Total Received: NGN 500.00' in notice['text']
        assert 'Outstanding Balance: NGN 500.00' in notice['text']
        assert notice['text'].count('**NGN 300.00 | 2026-01-03**')==1
        assert 'Historical received: NGN 200.00' in notice['text']
        assert 'Total Packages: **5 PCS**' in notice['text']
        for t in tickets:
            db.add(HoldFact(ticket_id=t.id,original_phone='Synthetic supplier hold',source_version=t.version,state='removed',removed_by=actor.id))
        db.flush()
        removed=call(db,actor,notifications.render,kind='remove_hold',ticket_ids=[t.id for t in tickets])
        assert 'Total Payable: NGN 1000.00' in removed['text']
        assert len(list(db.scalars(select(Payment))))==1


def test_final_i06_shared_invoice_across_destinations_counts_money_once(app):
    from app.models import Warehouse
    source=sample_source();ships=json.loads(source['data']['logistics_shipments'])
    for t,amount in zip(ships[0]['tickets'],(400,600)):t.update(freightUSD=0,clearingNGN=amount)
    source['data']['logistics_shipments']=json.dumps(ships)
    bills=json.loads(source['data']['logistics_bills']);bills[0].update(amount=1000,received=200,discount=0)
    source['data']['logistics_bills']=json.dumps(bills)
    with app.state.session_factory() as db,db.begin():
        actor,wh,party,bill,tickets=sourced_group(app,db,source)
        second=Warehouse(name='Synthetic second destination',address='Synthetic address two',contact='Synthetic contact two')
        db.add(second);db.flush()
        invoice=db.get(Invoice,bill.native_id)
        pay=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount='300',currency='NGN',paid_on='2026-01-03')
        call(db,actor,finance.post_payment,id=pay['id'],expected_version=1,approval='manual',allocations=[dict(invoice_id=invoice.id,expected_version=invoice.version,amount_ngn='300')])
        for t,destination in zip(tickets,(wh,second.id)):
            migration.resolve_charge_basis(db,actor,t.id,dict(expected_version=t.version,source='Explicit original CBM',charge_components=[dict(component='clearing',currency='NGN',unit='CBM',amount=str(t.clearing_ngn),source='Original charge')],idempotency_key='basis-'+t.id))
            t.destination_warehouse_id=destination
            call(db,actor,packing.arrive,id=t.id,expected_version=t.version,pieces=t.pieces,kg=None,cbm=str(t.cbm),occurred_on='2026-01-02',source='Actual synthetic arrival',mode='sea')
            call(db,actor,tracking.set_node,target_type='ticket',target_id=t.id,expected_version=t.version,node='warehouse_arrived',mode='manual',destination_warehouse_id=destination,occurred_at='2026-01-03T10:00:00+08:00',source='Actual synthetic warehouse confirmation')
        order=call(db,actor,release.issue_order,members=[dict(ticket_id=t.id,expected_version=t.version) for t in tickets],mode='credit',promise_on='2026-12-01',guarantor='Synthetic guarantor')
        notice=call(db,actor,notifications.render,kind='release',ticket_ids=[t.id for t in tickets],order_id=order['id'])
        text=notice['text']
        assert text.count('Full Original Invoice Payable Amount: NGN 1000.00')==1
        assert text.count('Total Received: NGN 500.00')==1
        assert text.count('Outstanding Balance: NGN 500.00')==1
        assert text.count('NGN 300.00 | 2026-01-03')==1
        assert text.count('SHARED INVOICE FINANCIAL SUMMARY')==1
        for t in tickets:assert t.receipt in text
        assert 'Synthetic second destination' in text
        for t in tickets:
            db.add(HoldFact(ticket_id=t.id,original_phone='Synthetic supplier hold',source_version=t.version,state='removed',removed_by=actor.id))
        db.flush()
        removed=call(db,actor,notifications.render,kind='remove_hold',ticket_ids=[t.id for t in tickets])
        assert removed['text'].count('Total Payable: NGN 1000.00')==1
        assert removed['text'].count('SHARED INVOICE FINANCIAL SUMMARY')==1
