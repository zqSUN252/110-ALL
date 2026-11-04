from decimal import Decimal
import pytest
from sqlalchemy import select
from app.common import Actor, DomainError
from app.models import User
from app.finance import service as finance


def actor(db):
    return Actor(db.scalar(select(User.id).where(User.username == 'admin')), {'*'}, set())


def call(db, who, fn, **payload):
    from uuid import uuid4
    return fn(db, who, dict(idempotency_key=uuid4().hex, **payload))


def seed(db, who):
    warehouse = call(db, who, finance.create_warehouse, name='Synthetic warehouse')
    party = call(db, who, finance.create_party, name='Synthetic party', warehouse_id=warehouse['id'])
    return warehouse['id'], party['id']


def test_exact_currency_and_units():
    assert finance.charge_amount('2000', 'NGN', 'PCS', '3', {}) == Decimal('6000.00')
    assert finance.settle('10', 'USD', {'direction':'NGN_PER_USD','rate':'1500'}) == Decimal('15000.00')
    assert finance.settle('70', 'CNY', {'direction':'USD_CNY_CROSS','ngn_per_usd':'1500','cny_per_usd':'7'}) == Decimal('15000.00')
    with pytest.raises(DomainError):
        finance.settle('70', 'CNY', {'direction':'NGN_PER_USD','rate':'1500'})


@pytest.mark.parametrize('new_amount,allocated,available,outstanding', [('80000','80000.00','20000.00','0.00'),('120000','100000.00','0.00','20000.00')])
def test_paid_reprice_preserves_cash(app, new_amount, allocated, available, outstanding):
    with app.state.session_factory() as db, db.begin():
        who=actor(db); warehouse, party=seed(db,who)
        invoice=call(db,who,finance.create_invoice,party_id=party,warehouse_id=warehouse,amount_ngn='100000',reference='SYN-1',uploaded_on='2026-11-01')
        payment=call(db,who,finance.create_payment,party_id=party,warehouse_id=warehouse,amount='100000',currency='NGN',paid_on='2026-11-01')
        posted=call(db,who,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':invoice['id'],'expected_version':1,'amount_ngn':'100000'}])
        preview=finance.adjustment_preview(db,who,invoice['id'],new_amount)
        changed=call(db,who,finance.adjust_invoice,id=invoice['id'],expected_version=2,amount_ngn=new_amount,reason='price correction',preview=preview)
        assert changed['allocated_ngn']==allocated and changed['outstanding_ngn']==outstanding
        assert changed['due_on']=='2026-11-15'
        current=finance.get_payment(db,who,posted['id'])
        assert current['amount_ngn']=='100000.00' and current['available_ngn']==available


def test_dual_invoice_discount_opening_payables_and_approval_dates(app):
    from app.models import Payment,utcnow
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,party=seed(db,who)
        inv=call(db,who,finance.create_invoice,party_id=party,warehouse_id=wh,reference='SYN-USD-NGN',amount_ngn='20000',charge_usd='10',charge_ngn='5000',ngn_per_usd='1500',uploaded_on='2026-11-01')
        assert inv['charge_usd']=='10.00' and inv['ngn_per_usd']=='1500.000000'
        call(db,who,finance.add_discount,id=inv['id'],expected_version=1,amount_ngn='1000')
        pay=call(db,who,finance.create_payment,party_id=party,warehouse_id=wh,amount='19000',currency='NGN',paid_on='2026-11-01')
        posted=call(db,who,finance.post_payment,id=pay['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':inv['id'],'expected_version':2,'amount_ngn':'19000'}])
        assert posted['approval']=='manual' and posted['bank_state']=='unverified'
        opening=call(db,who,finance.create_opening_balance,party_id=party,warehouse_id=wh,amount_ngn='500',kind='credit',as_of='2020-01-01',source='synthetic opening evidence')
        historic=db.get(Payment,opening['payment_id'])
        assert historic.currency is None and historic.amount is None and historic.approved_at is None
        today=finance.totals(db,who,wh)
        assert today['today_ngn']=='19000.00' and today['unknown_approval_count']==1
        tomorrow=(utcnow().astimezone(ZoneInfo('Asia/Shanghai')).date()+timedelta(days=1)).isoformat()
        assert finance.totals(db,who,wh,on=tomorrow)['today_ngn']=='0.00'
        sample=call(db,who,finance.create_invoice,party_id=party,warehouse_id=wh,scope='sample',reference='SAMPLE',amount_ngn='100',uploaded_on='2026-11-01')
        samplepay=call(db,who,finance.create_payment,party_id=party,warehouse_id=wh,scope='sample',amount='100',currency='NGN')
        call(db,who,finance.post_payment,id=samplepay['id'],expected_version=1,allocations=[{'invoice_id':sample['id'],'expected_version':1,'amount_ngn':'100'}],approval='manual')
        assert finance.totals(db,who,wh)['today_ngn']=='19000.00'
        assert finance.totals(db,who,wh,'sample')['today_ngn']=='100.00'
        call(db,who,finance.create_invoice,party_id=party,warehouse_id=wh,kind='AP',period='2026-11',reference='SUPPLIER',amount_ngn='10000',uploaded_on='2026-11-01')
        paid=call(db,who,finance.pay_payable,party_id=party,warehouse_id=wh,period='2026-11',amount='7000',currency='NGN',paid_on='2026-11-02')
        assert paid['allocated_ngn']=='0.00'
        assert finance.payable_period(db,who,wh,party,'2026-11')['balance_ngn']=='3000.00'


def test_price_below_discount_requires_explicit_same_command_resolution(app):
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,party=seed(db,who)
        inv=call(db,who,finance.create_invoice,party_id=party,warehouse_id=wh,reference='SYN',amount_ngn='10000',uploaded_on='2026-11-01')
        call(db,who,finance.add_discount,id=inv['id'],expected_version=1,amount_ngn='5000')
        preview=finance.adjustment_preview(db,who,inv['id'],'1000','1000')
        adjusted=call(db,who,finance.adjust_invoice,id=inv['id'],expected_version=2,amount_ngn='1000',discount_total_ngn='1000',reason='Original amount corrected with explicit discount resolution',preview=preview)
        assert adjusted['discount_ngn']=='1000.00' and adjusted['outstanding_ngn']=='0.00'


def test_finance_http_csrf_scope_atomic_rollback_and_replay_permission(app):
    from fastapi.testclient import TestClient
    from app.models import Grant, WarehouseScope, Payment,Allocation
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,party=seed(db,who)
        invs=[call(db,who,finance.create_invoice,party_id=party,warehouse_id=wh,reference=str(n),amount_ngn='100',uploaded_on='2026-11-01') for n in range(2)]
        pay=call(db,who,finance.create_payment,party_id=party,warehouse_id=wh,amount='100',currency='NGN')
        operator=db.scalar(select(User).where(User.username=='operator'))
        db.add(Grant(user_id=operator.id,capability='finance.post'))
        operator_id=operator.id
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        payload={'expected_version':1,'approval':'manual','allocations':[{'invoice_id':i['id'],'expected_version':1,'amount_ngn':'100'} for i in invs]}
        url=f"/api/payments/{pay['id']}/post"
        assert client.post(url,json=payload,headers={'Idempotency-Key':'synthetic-bad'}).status_code==403
        headers={'X-CSRF-Token':login['csrf_token'],'Idempotency-Key':'synthetic-bad'}
        rejected=client.post(url,json=payload,headers=headers)
        assert rejected.status_code==409,rejected.text
        with app.state.session_factory() as db:
            assert db.get(Payment,pay['id']).state=='pending'
            assert not list(db.scalars(select(Allocation)))
        other=client.post('/api/auth/login',json={'username':'operator','password':'synthetic-password-123'}).json()
        headers={'X-CSRF-Token':other['csrf_token'],'Idempotency-Key':'synthetic-replay'}
        payload['allocations']=payload['allocations'][:1]
        assert client.post(url,json=payload,headers=headers).status_code==403

        with app.state.session_factory() as db,db.begin():db.add(WarehouseScope(user_id=operator_id,warehouse_id=wh))
        first=client.post(url,json=payload,headers=headers);assert first.status_code==200,first.text
        assert client.post(url,json=payload,headers=headers).json()==first.json()
        with app.state.session_factory() as db,db.begin():
            from sqlalchemy import delete
            db.execute(delete(Grant).where(Grant.user_id==operator_id,Grant.capability=='finance.post'))
        assert client.post(url,json=payload,headers=headers).status_code==403


def test_many_payments_one_invoice_reverse_and_approved_excel(app):
    from app.finance.export import export_approved
    from app.models import Payment,Allocation
    from datetime import datetime
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,party=seed(db,who)
        invoice=call(db,who,finance.create_invoice,party_id=party,warehouse_id=wh,reference='=SYNTHETIC',amount_ngn='300',uploaded_on='2026-11-01')
        payments=[]
        for n in range(3):
            payment=call(db,who,finance.create_payment,party_id=party,warehouse_id=wh,amount='100',currency='NGN')
            payments.append(call(db,who,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':invoice['id'],'expected_version':n+1,'amount_ngn':'100'}]))
        assert finance.get_invoice(db,who,invoice['id'])['outstanding_ngn']=='0.00'
        selected=finance.totals(db,who,wh)['day']
        exported=call(db,who,export_approved,warehouse_id=wh,on=selected)
        assert exported['count']==3 and exported['amount_ngn']=='300.00' and exported['round_trip_verified']
        reversed=call(db,who,finance.reverse_payment,id=payments[0]['id'],expected_version=2,reason='Explicit synthetic reversal',confirmed=True)
        assert reversed['state']=='reversed' and reversed['amount_ngn']=='100.00'
        assert finance.get_invoice(db,who,invoice['id'])['outstanding_ngn']=='100.00'
        assert finance.totals(db,who,wh)['today_ngn']=='200.00'
