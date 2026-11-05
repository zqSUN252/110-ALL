from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from app.models import Invoice,Payment,Allocation,Discount,Ticket
from app.finance import service as finance
from app.packing import service as packing
from app.common import DomainError
from test_logistics import who,call,seed
from test_packing import make_document
from test_final_wave import arrive


def setup_pair(db,actor):
    wh,party=seed(db,actor);doc,a=make_document(db,actor,wh,party)
    a=call(db,actor,packing.update_ticket,id=a['id'],expected_version=a['version'],freight_usd='0',clearing_ngn='1000',charge_components=[dict(component='freight',currency='USD',unit='KG',amount='0',source='Synthetic'),dict(component='clearing',currency='NGN',unit='CBM',amount='1000',source='Synthetic')])
    b=call(db,actor,packing.create_ticket,document_id=doc['id'],party_id=party,receipt='Synthetic B',mark='B',pieces=1,kg='1',cbm='1',freight_usd='0',clearing_ngn='500',charge_components=[dict(component='freight',currency='USD',unit='KG',amount='0',source='Synthetic'),dict(component='clearing',currency='NGN',unit='CBM',amount='500',source='Synthetic')])
    a=arrive(db,actor,a);b=arrive(db,actor,b)
    inv=finance.get_invoice(db,actor,a['invoice_id'])
    call(db,actor,finance.add_discount,id=inv['id'],expected_version=inv['version'],amount_ngn='50',reason='Synthetic discount')
    for t,amount in ((a,'600'),(b,'200')):
        inv=finance.get_invoice(db,actor,t['invoice_id'])
        pay=call(db,actor,finance.create_payment,warehouse_id=wh,party_id=party,amount=amount,currency='NGN')
        call(db,actor,finance.post_payment,id=pay['id'],expected_version=pay['version'],approval='manual',allocations=[dict(invoice_id=inv['id'],expected_version=inv['version'],amount_ngn=amount)])
    return doc,a,b


def test_native_merge_and_explicit_split_preserve_each_cash_source(app):
    from app.packing import groups
    with app.state.session_factory() as db,db.begin():
        actor=who(db);doc,a,b=setup_pair(db,actor)
        original={p.id:(p.party_id,p.currency,p.amount,p.amount_ngn) for p in db.scalars(select(Payment))}
        due=db.get(Invoice,a['invoice_id']).due_on
        rows=list(db.scalars(select(Allocation).where(Allocation.amount_ngn>0)))
        plan={'groups':[{'key':'combined','ticket_ids':[a['id'],b['id']],'invoice_id':a['invoice_id']}],
              'allocations':[{'allocation_id':r.id,'targets':[{'key':'combined','amount_ngn':str(r.amount_ngn)}]} for r in rows],
              'discounts':[{'invoice_id':a['invoice_id'],'targets':[{'key':'combined','amount_ngn':'50'}]}]}
        pv=groups.preview(db,actor,doc['id'],plan)
        payload=dict(id=doc['id'],expected_version=pv['document']['version'],plan=plan,preview=pv,reason='Explicit synthetic merge',idempotency_key=uuid4().hex)
        result=groups.confirm(db,actor,payload)
        inv=finance.get_invoice(db,actor,a['invoice_id'])
        assert (inv['amount_ngn'],inv['discount_ngn'],inv['allocated_ngn'],inv['outstanding_ngn'])==('1500.00','50.00','800.00','650.00')
        assert db.get(Invoice,b['invoice_id']).amount_ngn==0 and db.get(Invoice,b['invoice_id']).state=='merged'
        assert db.get(Ticket,a['id']).invoice_id==db.get(Ticket,b['id']).invoice_id==inv['id']
        assert db.get(Invoice,inv['id']).due_on==due
        assert groups.confirm(db,actor,payload)==result
        current=[r for r in db.scalars(select(Allocation).where(Allocation.amount_ngn>0)) if r.amount_ngn+finance.total(db,Allocation,'amount_ngn',reverses_id=r.id)>0]
        split={'groups':[{'key':'a','ticket_ids':[a['id']],'invoice_id':inv['id']},{'key':'b','ticket_ids':[b['id']],'invoice_id':None}],
               'allocations':[{'allocation_id':r.id,'targets':[{'key':'a' if r.amount_ngn==600 else 'b','amount_ngn':str(r.amount_ngn)}]} for r in current],
               'discounts':[{'invoice_id':inv['id'],'targets':[{'key':'a','amount_ngn':'50'}]}]}
        pv=groups.preview(db,actor,doc['id'],split)
        call(db,actor,groups.confirm,id=doc['id'],expected_version=pv['document']['version'],plan=split,preview=pv,reason='Explicit synthetic split')
        a_inv=finance.get_invoice(db,actor,db.get(Ticket,a['id']).invoice_id);b_inv=finance.get_invoice(db,actor,db.get(Ticket,b['id']).invoice_id)
        assert (a_inv['amount_ngn'],b_inv['amount_ngn'])==('1000.00','500.00')
        assert sum(Decimal(x['outstanding_ngn']) for x in (a_inv,b_inv))==Decimal('650')
        assert original=={p.id:(p.party_id,p.currency,p.amount,p.amount_ngn) for p in db.scalars(select(Payment))}
        # Cancel/recover the current two groups: no new cash, original discounts
        # restored explicitly, old allocations remain original-payer balances.
        pv=packing.void_preview(db,actor,doc['id'])
        call(db,actor,packing.void_document,id=doc['id'],expected_version=pv['version'],preview=pv,reason='Synthetic group cancellation')
        pv=packing.recover_preview(db,actor,doc['id'])
        call(db,actor,packing.recover_document,id=doc['id'],expected_version=pv['version'],preview=pv,reason='Synthetic group recovery')
        active=[finance.get_invoice(db,actor,db.get(Ticket,ticket['id']).invoice_id) for ticket in (a,b)]
        assert sum(Decimal(x['discount_ngn']) for x in active)==50
        assert sum(Decimal(finance.payment_json(db,p)['available_ngn']) for p in db.scalars(select(Payment)))==800
        # Recovered group invoices accept a fresh explicit allocation; recovery
        # itself never auto-allocates cash.
        outstanding=sum(Decimal(finance.get_invoice(db,actor,x['id'])['outstanding_ngn']) for x in active)
        assert outstanding==Decimal('1450')
        allocations=[]
        for x in active:
            inv=finance.get_invoice(db,actor,x['id']);due=Decimal(inv['outstanding_ngn'])
            if due:allocations.append(dict(invoice_id=inv['id'],expected_version=inv['version'],amount_ngn=str(due)))
        ticket=db.get(Ticket,a['id'])
        pay=call(db,actor,finance.create_payment,warehouse_id=ticket.warehouse_id,party_id=ticket.party_id,amount=str(outstanding),currency='NGN')
        call(db,actor,finance.post_payment,id=pay['id'],expected_version=pay['version'],approval='manual',allocations=allocations)
        assert sum(Decimal(finance.get_invoice(db,actor,x['id'])['outstanding_ngn']) for x in active)==0


def test_native_new_member_does_not_join_manual_invoice_group(app):
    from app.models import NativeBillingGroup
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);doc,a=make_document(db,actor,wh,party)
        a=call(db,actor,packing.update_ticket,id=a['id'],expected_version=a['version'],mark='SAME')
        at=db.get(Ticket,a['id']);g=db.get(NativeBillingGroup,at.billing_group_id)
        manual=call(db,actor,finance.create_invoice,party_id=party,warehouse_id=wh,reference='Synthetic manual invoice',amount_ngn='1000',uploaded_on='2026-11-01',source_key='manual:synthetic')
        g.invoice_id=manual['id'];at.invoice_id=manual['id'];db.flush()
        b=call(db,actor,packing.create_ticket,document_id=doc['id'],party_id=party,receipt='SYN-MANUAL-B',mark='SAME',pieces=1,kg='1',cbm='1',freight_usd='0',clearing_ngn='500',
               charge_components=[dict(component='freight',currency='USD',unit='KG',amount='0',source='Synthetic'),dict(component='clearing',currency='NGN',unit='CBM',amount='500',source='Synthetic')])
        assert db.get(Ticket,b['id']).billing_group_id!=g.id
        b=arrive(db,actor,b)
        assert b['invoice_id']!=manual['id']
        assert finance.get_invoice(db,actor,b['invoice_id'])['amount_ngn']=='500.00'
        assert finance.get_invoice(db,actor,manual['id'])['amount_ngn']=='1000.00'


def test_shared_invoice_read_requires_every_member_warehouse(app):
    from app.common import Actor
    from app.finance.access import invoice_access,visible_invoices
    from app.models import Warehouse
    with app.state.session_factory() as db,db.begin():
        actor=who(db);doc,a,b=setup_pair(db,actor)
        other=Warehouse(name='Synthetic other shared warehouse');db.add(other);db.flush()
        first=db.get(Ticket,a['id']);second=db.get(Ticket,b['id'])
        second.warehouse_id=other.id;second.invoice_id=first.invoice_id;db.flush()
        invoice=db.get(Invoice,first.invoice_id)
        limited=Actor('synthetic-limited',{'finance.read','packing.read'},{first.warehouse_id})
        with pytest.raises(DomainError):invoice_access(db,limited,invoice,'finance.read')
        assert db.scalar(visible_invoices(select(Invoice.id).where(Invoice.id==invoice.id),limited)) is None
        full=Actor('synthetic-full',{'finance.read','packing.read'},{first.warehouse_id,other.id})
        assert db.scalar(visible_invoices(select(Invoice.id).where(Invoice.id==invoice.id),full))==invoice.id


def test_group_preview_concurrent_with_invoice_read_has_no_deadlock(app):
    from concurrent.futures import ThreadPoolExecutor
    from app.packing import groups
    with app.state.session_factory() as db,db.begin():
        actor=who(db);doc,a,b=setup_pair(db,actor);invoice_id=a['invoice_id']
    errors=[]
    def run(index):
        try:
            with app.state.session_factory() as db,db.begin():
                actor=who(db)
                if index%2:groups.preview(db,actor,doc['id'],{})
                else:finance.get_invoice(db,actor,invoice_id)
        except Exception as exc:errors.append(repr(exc))
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(run,range(20)))
    assert errors==[]


def test_editing_old_ungrouped_ticket_to_same_identity_regroups(app):
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);doc,a=make_document(db,actor,wh,party)
        a=call(db,actor,packing.update_ticket,id=a['id'],expected_version=a['version'],mark='SAME',contact_raw='+2347000000000',phone_normalized='+2347000000000',phone_confirmed=True,destination='LAGOS')
        b=call(db,actor,packing.create_ticket,document_id=doc['id'],party_id=party,receipt='SYN-OLD-2',mark='SAME',contact_raw='+2347000000000',phone_normalized='+2347000000000',phone_confirmed=True,destination='LAGOS',pieces=1,kg='1',cbm='1')
        first=db.get(Ticket,a['id']);group=first.billing_group_id
        first.billing_group_id=None;db.flush()
        call(db,actor,packing.update_ticket,id=a['id'],expected_version=first.version,mark='SAME',reason='Synthetic legacy regroup')
        assert db.get(Ticket,a['id']).billing_group_id==db.get(Ticket,b['id']).billing_group_id==group


def test_group_preview_rejects_stale_cash_source(app):
    from app.packing import groups
    with app.state.session_factory() as db,db.begin():
        actor=who(db);doc,a,b=setup_pair(db,actor)
        pv=groups.preview(db,actor,doc['id'],{})
        assert pv['requires_plan'] and len(pv['allocations'])==2
        pay=db.scalar(select(Payment));pay.version+=1;db.flush()
        with pytest.raises(DomainError,match='changed|complete'):
            call(db,actor,groups.confirm,id=doc['id'],expected_version=pv['document']['version'],plan={},preview=pv,reason='Stale synthetic source')
