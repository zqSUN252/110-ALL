"""Command services shared by HTTP, authorized chat, and cargo orchestration.

No commits, external I/O, or physical release changes happen here.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, text
from app.common import DomainError, require, check_version, audit, run_command, json_value
from app.models import (Warehouse, Party, Invoice, InvoiceAdjustment, Discount, Payment, Allocation,
    OpeningBalance, BankClaim, EvidenceCase, FinanceEvent, PartyWarehouse, BankAccount, CrossPartyAuthorization, utcnow)
from .access import party_member,account_access,payment_access,visible_payments

ZERO = Decimal('0.00')


def decimal(value, places=2, positive=False):
    try:
        if isinstance(value, (float, bool)):
            raise ValueError()
        number = Decimal(str(value))
        quantum = Decimal(1).scaleb(-places)
        if not number.is_finite() or abs(number) >= Decimal('1e14') or number != number.quantize(quantum) or number < 0 or (positive and number == 0):
            raise ValueError()
        return number.quantize(quantum)
    except (ValueError, InvalidOperation):
        raise DomainError('invalid_decimal', 'Provide a finite nonnegative decimal string with valid precision') from None


def settle(amount, currency, fx=None):
    amount = decimal(amount); fx = fx or {}
    direction = fx.get('direction')
    allowed={'NATIVE':{'direction'},'NGN_PER_USD':{'direction','rate'},'NGN_PER_CNY':{'direction','rate'},'USD_CNY_CROSS':{'direction','ngn_per_usd','cny_per_usd'}}
    if fx and (direction not in allowed or set(fx)!=allowed[direction]):
        raise DomainError('invalid_fx','Rate fields must exactly match their explicit direction')
    if currency == 'NGN' and direction in (None, 'NATIVE'):
        if any(k in fx for k in ('rate','ngn_per_usd','cny_per_usd')):
            raise DomainError('invalid_fx', 'Native NGN has rate 1; no conversion inputs')
        return amount
    if currency == 'USD' and direction == 'NGN_PER_USD':
        result = amount * decimal(fx.get('rate'), 6, True)
    elif currency == 'CNY' and direction == 'NGN_PER_CNY':
        result = amount * decimal(fx.get('rate'), 6, True)
    elif currency == 'CNY' and direction == 'USD_CNY_CROSS':
        result = amount * decimal(fx.get('ngn_per_usd'), 6, True) / decimal(fx.get('cny_per_usd'), 6, True)
    else:
        raise DomainError('invalid_fx', 'Explicit supported currency and rate direction required')
    return decimal(result.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def charge_amount(unit_price, currency, unit, quantity, fx):
    if unit not in ('KG', 'PCS', 'CBM') or currency not in ('USD','NGN'):
        raise DomainError('invalid_charge', 'Use USD/NGN and explicit KG/PCS/CBM units')
    quantity = decimal(quantity, 6)
    if unit == 'PCS' and quantity != quantity.to_integral_value():
        raise DomainError('invalid_quantity', 'PCS must be whole pieces')
    return settle((decimal(unit_price) * quantity).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), currency, fx)


def scope(value):
    if value not in ('live','sample'):
        raise DomainError('invalid_scope', 'Choose live or sample')
    return value


def day(value):
    try:
        return date.fromisoformat(value) if isinstance(value,str) else value if isinstance(value,date) and not isinstance(value,datetime) else (_ for _ in ()).throw(ValueError())
    except ValueError:
        raise DomainError('invalid_date','Use ISO calendar date') from None


def command(name, fields):
    """Bound request contract plus transactional idempotency for every entry point."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(db, actor, payload):
            unknown=set(payload)-set(fields.split())-{'idempotency_key'}
            if unknown:
                raise DomainError('unknown_fields','Unsupported command fields',422,{'fields':sorted(unknown)})
            # Keep related-resource authorization stable until either fresh work
            # or replay completes. Acquire the shared order guard before command
            # key locks in every domain path, including nested channel actions.
            db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
            authorize_command(db,actor,name,payload)
            def apply():
                try:
                    result=fn(db,actor,payload)
                except KeyError:
                    raise DomainError('missing_field','Required command field missing',422) from None
                db.flush()
                result=json_value(result)
                audit(db,actor,name,name.split('.')[0],result.get('id','batch'),None,result)
                return result
            result=run_command(db,actor,name,payload.get('idempotency_key'),payload,apply)
            from .authorization import authorize_response
            authorize_response(db,actor,name,result)
            return result
        return wrapped
    return decorate


def authorize_command(db,actor,name,p):
    from .authorization import authorize
    authorize(db,actor,name,p)


def locked(db, cls, identity):
    obj=db.scalar(select(cls).where(cls.id==identity).with_for_update().execution_options(populate_existing=True))
    if obj is None:
        raise DomainError('not_found','Record not found',404)
    return obj


def access(db, actor, cls, identity, capability, expected=None):
    obj=locked(db,cls,identity)
    if isinstance(obj,BankAccount):account_access(db,actor,obj,capability)
    elif isinstance(obj,Payment):payment_access(db,actor,obj,capability)
    else:require(actor,capability,getattr(obj,'warehouse_id',None))
    if expected is not None:
        check_version(obj,expected)
    return obj


def record(obj):
    values={c.name:getattr(obj,c.name) for c in obj.__table__.columns}
    return json_value({k:v.astimezone(timezone.utc) if isinstance(v,datetime) and v.tzinfo else v for k,v in values.items()})


def total(db, cls, field, **filters):
    return db.scalar(select(func.coalesce(func.sum(getattr(cls,field)),0)).filter_by(**filters)) or ZERO


def invoice_json(db, inv):
    allocated=total(db,Allocation,'amount_ngn',invoice_id=inv.id)
    discount=total(db,Discount,'amount_ngn',invoice_id=inv.id)
    return record(inv) | json_value({'allocated_ngn':allocated,'discount_ngn':discount,'outstanding_ngn':inv.amount_ngn-discount-allocated})


def payment_json(db,payment):
    allocated=total(db,Allocation,'amount_ngn',payment_id=payment.id)
    return record(payment)|json_value({'allocated_ngn':allocated,'available_ngn':payment.amount_ngn-allocated if payment.state=='posted' else ZERO})


def get_invoice(db,actor,identity):
    inv=locked(db,Invoice,identity)
    require(actor,'payables.read' if inv.kind=='AP' else 'finance.read',inv.warehouse_id)
    return invoice_json(db,inv)


def get_payment(db,actor,identity):
    payment=locked(db,Payment,identity)
    payment_access(db,actor,payment,'payables.read' if payment.direction=='out' else 'finance.read')
    return payment_json(db,payment)


def party_scope(db,actor,payload,cap):
    require(actor,cap,payload['warehouse_id'])
    locked(db,Party,payload['party_id']); party_member(db,payload['party_id'],payload['warehouse_id'])
    return scope(payload.get('scope','live'))


@command('warehouse.create','name address contact source')
def create_warehouse(db,actor,p):
    require(actor,'warehouses.manage')
    obj=Warehouse(**{k:v for k,v in p.items() if k!='idempotency_key'})
    db.add(obj); db.flush(); return record(obj)


@command('party.create','name warehouse_id classification phone address source categories phone_source')
def create_party(db,actor,p):
    require(actor,'parties.manage',p['warehouse_id'])
    locked(db,Warehouse,p['warehouse_id'])
    obj=Party(**{k:v for k,v in p.items() if k not in ('idempotency_key','categories','phone_source')})
    db.add(obj); db.flush()
    db.add(PartyWarehouse(party_id=obj.id,warehouse_id=obj.warehouse_id,source=p.get('source') or 'Explicit home warehouse on creation'))
    from app.models import PartyPhone,PartyCategory
    from app.warehouses.masters import normalized_phone,party_json
    if p.get('phone'):
        if not str(p.get('phone_source') or p.get('source') or '').strip():raise DomainError('phone_source','New phone needs original source or explicit manual provenance')
        db.add(PartyPhone(party_id=obj.id,raw=p['phone'],normalized=normalized_phone(p['phone']),source=p.get('phone_source') or p['source'],actor_id=actor.id))
    categories=p.get('categories',[])
    if not isinstance(categories,list) or len(categories)>30 or any(not isinstance(c,str) or not c.strip() or len(c)>100 for c in categories):raise DomainError('categories','Provide explicit categories')
    db.add_all([PartyCategory(party_id=obj.id,category=c) for c in set(categories)])
    db.flush(); return party_json(db,obj)


@command('party.membership','id expected_version warehouse_id active source')
def set_party_membership(db,actor,p):
    party=access(db,actor,Party,p['id'],'parties.manage');check_version(party,p.get('expected_version'))
    require(actor,'parties.manage',p['warehouse_id']);locked(db,Warehouse,p['warehouse_id'])
    if not isinstance(p.get('active'),bool) or not str(p.get('source','')).strip():raise DomainError('membership_evidence','Explicit participation state and source required')
    membership=db.scalar(select(PartyWarehouse).where(PartyWarehouse.party_id==party.id,PartyWarehouse.warehouse_id==p['warehouse_id']).with_for_update())
    if membership:membership.active=p['active'];membership.source=p['source'];membership.version+=1
    else:db.add(PartyWarehouse(party_id=party.id,warehouse_id=p['warehouse_id'],active=p['active'],source=p['source']))
    party.version+=1;db.flush();return record(party)|{'memberships':[record(m) for m in db.scalars(select(PartyWarehouse).where(PartyWarehouse.party_id==party.id))]}


@command('invoice.create','party_id warehouse_id scope kind reference amount_ngn charge_usd charge_ngn ngn_per_usd uploaded_on period source_key pricing rounding_basis')
def create_invoice(db,actor,p):
    kind=p.get('kind','AR')
    if kind not in ('AR','AP'):
        raise DomainError('invalid_kind','Invoice kind must be AR or AP')
    mode=party_scope(db,actor,p,'payables.manage' if kind=='AP' else 'finance.invoice')
    if kind=='AP' and not p.get('period'):
        raise DomainError('period_required','Payables require a billed period')
    uploaded=day(p['uploaded_on'])
    amount=decimal(p['amount_ngn']); usd=ngn=rate=None;rounding=ZERO;rounding_basis=None
    if 'rounding_basis' in p:
        from .rounding import validate_basis
        rounding,rounding_basis=validate_basis(db,p['rounding_basis'],amount,payload=p)
    if any(key in p for key in ('charge_usd','charge_ngn','ngn_per_usd')):
        usd=decimal(p.get('charge_usd','0')); ngn=decimal(p.get('charge_ngn','0'))
        rate=decimal(p['ngn_per_usd'],6,True) if usd else None
        calculated=settle(usd,'USD',{'direction':'NGN_PER_USD','rate':str(rate)})+ngn if usd else ngn
        if calculated+rounding!=amount:raise DomainError('charge_mismatch','USD × explicit NGN/USD + native NGN + validated rounding must equal invoice total')
    inv=Invoice(party_id=p['party_id'],warehouse_id=p['warehouse_id'],scope=mode,kind=kind,reference=p['reference'],
        amount_ngn=amount,charge_usd=usd,charge_ngn=ngn,ngn_per_usd=rate,rounding_adjustment_ngn=rounding,uploaded_on=uploaded,due_on=uploaded+timedelta(days=14),period=p.get('period'),
        source_key=p.get('source_key'),pricing=p.get('pricing',{}))
    db.add(inv); db.flush()
    if rounding_basis:
        from .rounding import save_basis
        save_basis(db,actor,inv,rounding_basis)
    return invoice_json(db,inv)


def new_payment(db,actor,p,direction='in',state='pending'):
    mode=party_scope(db,actor,p,'payables.pay' if direction=='out' else 'finance.payment')
    fx=p.get('fx') or {}; value=decimal(p['amount'],positive=True)
    amount_ngn=settle(value,p['currency'],fx)
    if amount_ngn<=0:
        raise DomainError('invalid_amount','Settlement must be positive')
    payment=Payment(party_id=p['party_id'],warehouse_id=p['warehouse_id'],scope=mode,direction=direction,
        amount=value,currency=p['currency'],amount_ngn=amount_ngn,fx_direction=fx.get('direction','NATIVE'),
        fx_rate=decimal(fx['rate'],6,True) if 'rate' in fx else None,
        ngn_per_usd=decimal(fx['ngn_per_usd'],6,True) if 'ngn_per_usd' in fx else None,
        cny_per_usd=decimal(fx['cny_per_usd'],6,True) if 'cny_per_usd' in fx else None,
        paid_on=day(p['paid_on']) if p.get('paid_on') else None,period=p.get('period'),state=state,source_key=p.get('source_key'))
    db.add(payment); db.flush(); return payment


@command('payment.create','party_id warehouse_id scope amount currency fx paid_on source_key period')
def create_payment(db,actor,p):
    return payment_json(db,new_payment(db,actor,p))


def invalidate(db,invoice_id=None,payment_id=None,kind='invoice_changed'):
    db.add(FinanceEvent(invoice_id=invoice_id,payment_id=payment_id,kind=kind))


def allocate(db,actor,payment,rows,cross_party=None):
    if not isinstance(rows,list) or not rows or len(rows)>200:
        raise DomainError('allocations_required','Explicit invoice allocations required, maximum 200')
    if len({r['invoice_id'] for r in rows})!=len(rows):
        raise DomainError('duplicate_invoice','Invoice appears twice in request')
    if payment.bank_state=='disputed':
        raise DomainError('payment_frozen','Resolve bank dispute before new allocation',409)
    beneficiaries={r['invoice_id']:locked(db,Invoice,r['invoice_id']).party_id for r in rows}
    different={iid:party for iid,party in beneficiaries.items() if party!=payment.party_id}
    if different:
        if not isinstance(cross_party,dict) or set(cross_party)!={'source','beneficiaries'} or not isinstance(cross_party.get('source'),str) or not cross_party['source'].strip():
            raise DomainError('cross_party_confirmation','Explicit source-backed beneficiary authorization required')
        authorized=cross_party.get('beneficiaries')
        if not isinstance(authorized,list) or any(not isinstance(r,dict) or set(r)!={'invoice_id','party_id'} for r in authorized):raise DomainError('cross_party_confirmation','Select exact invoices and beneficiary identities')
        if len(authorized)!=len(different) or {r['invoice_id']:r['party_id'] for r in authorized}!=different:
            raise DomainError('cross_party_confirmation','Authorization must exactly match this command beneficiaries')
        payment_access(db,actor,payment,'finance.cross_party')
        for iid in different:require(actor,'finance.cross_party',db.get(Invoice,iid).warehouse_id)
    elif cross_party is not None:raise DomainError('cross_party_confirmation','No different-party invoice needs this authorization')
    available=payment.amount_ngn-total(db,Allocation,'amount_ngn',payment_id=payment.id)
    for row in sorted(rows,key=lambda r:r['invoice_id']):
        if set(row)-{'invoice_id','expected_version','amount_ngn'}:
            raise DomainError('unknown_fields','Unsupported allocation fields',422)
        inv=access(db,actor,Invoice,row['invoice_id'],'payables.pay' if payment.direction=='out' else 'finance.post')
        check_version(inv,row.get('expected_version'))
        party_member(db,inv.party_id,inv.warehouse_id)
        if inv.scope!=payment.scope or inv.kind!=('AR' if payment.direction=='in' else 'AP') or inv.state!='open':
            raise DomainError('allocation_scope','Invoice and payment party, scope, direction or status differ')
        amount=decimal(row['amount_ngn'],positive=True)
        due=inv.amount_ngn-total(db,Discount,'amount_ngn',invoice_id=inv.id)-total(db,Allocation,'amount_ngn',invoice_id=inv.id)
        if amount>due or amount>available:
            raise DomainError('over_allocation','Allocation exceeds payment balance or invoice outstanding',409)
        db.add(Allocation(payment_id=payment.id,invoice_id=inv.id,amount_ngn=amount,actor_id=actor.id))
        if inv.id in different:
            permission=CrossPartyAuthorization(payment_id=payment.id,invoice_id=inv.id,beneficiary_party_id=inv.party_id,amount_ngn=amount,source=cross_party['source'],actor_id=actor.id)
            db.add(permission);db.flush();audit(db,actor,'payment.cross_party_authorized','cross_party_authorization',permission.id,None,record(permission))
        inv.version+=1; available-=amount; db.flush()


@command('payment.post','id expected_version approval allocations case_id case_version cross_party')
def post_payment(db,actor,p):
    payment=access(db,actor,Payment,p['id'],'finance.post'); check_version(payment,p.get('expected_version'))
    if payment.direction!='in' or payment.state not in ('pending','posted'):
        raise DomainError('invalid_state','Payment cannot be posted',409)
    if payment.state=='pending':
        approval=p.get('approval')
        if approval not in ('manual','bank'):
            raise DomainError('approval_required','Choose explicit manual or bank approval')
        if approval=='bank':
            bind_claim(db,actor,payment,p['case_id'],p.get('case_version'))
        payment.approval=approval; payment.approved_at=utcnow(); payment.approved_by=actor.id; payment.state='posted'
    allocate(db,actor,payment,p['allocations'],p.get('cross_party')); payment.version+=1
    return payment_json(db,payment)


def bind_claim(db,actor,payment,case_id,expected):
    require(actor,'bank.review',payment.warehouse_id)
    case=access(db,actor,EvidenceCase,case_id,'evidence.review'); check_version(case,expected)
    claim=db.scalar(select(BankClaim).where(BankClaim.case_id==case.id,BankClaim.state!='released').with_for_update())
    if not claim or claim.state=='disputed' or claim.payment_id not in (None,payment.id):
        raise DomainError('claim_unavailable','Bank claim unavailable',409)
    from app.models import BankEntry
    from app.bank.service import ensure_claim_current
    entry=locked(db,BankEntry,claim.entry_id)
    require(actor,'finance.post',case.warehouse_id);require(actor,'bank.review',case.warehouse_id)
    account_access(db,actor,db.get(BankAccount,entry.account_id),'bank.review',case.warehouse_id)
    ensure_claim_current(db,claim)
    if case.scope!=payment.scope or entry.currency!=payment.currency or entry.credit!=payment.amount or case.payment_id not in (None,payment.id):
        raise DomainError('evidence_mismatch','Bank cash, currency or evidence scope differ from payment')
    claim.payment_id=payment.id; claim.state='posted'; claim.version+=1
    case.payment_id=payment.id; case.version+=1; payment.bank_state='verified'


@command('payment.attach_bank','id expected_version case_id case_version')
def attach_bank_evidence(db,actor,p):
    payment=access(db,actor,Payment,p['id'],'finance.post'); check_version(payment,p.get('expected_version'))
    if payment.state!='posted':
        raise DomainError('invalid_state','Attach bank evidence to an existing approved payment',409)
    bind_claim(db,actor,payment,p['case_id'],p.get('case_version')); payment.version+=1
    return payment_json(db,payment)


def unallocate(db,actor,inv,amount,reason,payment_id=None):
    query=select(Allocation).where(Allocation.invoice_id==inv.id,Allocation.amount_ngn>0)
    if payment_id: query=query.where(Allocation.payment_id==payment_id)
    for allocation in db.scalars(query.order_by(Allocation.created_at.desc(),Allocation.id).with_for_update()):
        used=allocation.amount_ngn+total(db,Allocation,'amount_ngn',reverses_id=allocation.id)
        take=min(used,amount)
        if take>0:
            db.add(Allocation(payment_id=allocation.payment_id,invoice_id=inv.id,amount_ngn=-take,reverses_id=allocation.id,reason=reason,actor_id=actor.id))
            owner=locked(db,Payment,allocation.payment_id); owner.version+=1
            amount-=take; db.flush()
        if amount==0: break
    if amount!=0: raise DomainError('ledger_inconsistent','Allocation ledger inconsistent',409)


@command('invoice.adjust','id expected_version amount_ngn reason pricing preview discount_total_ngn charge_usd charge_ngn ngn_per_usd rounding_basis')
def adjust_invoice(db,actor,p):
    inv=locked(db,Invoice,p['id']); require(actor,'payables.manage' if inv.kind=='AP' else 'finance.adjust',inv.warehouse_id); check_version(inv,p.get('expected_version'))
    before=invoice_json(db,inv); new=decimal(p['amount_ngn'])
    old_discounts=total(db,Discount,'amount_ngn',invoice_id=inv.id)
    discounts=decimal(p['discount_total_ngn']) if 'discount_total_ngn' in p else old_discounts
    if discounts!=old_discounts:require(actor,'finance.discount',inv.warehouse_id)
    if discounts>new: raise DomainError('discount_exceeds_price','Existing discounts exceed new price; explicit discount resolution required')
    allocated=total(db,Allocation,'amount_ngn',invoice_id=inv.id)
    excess=max(ZERO,allocated+discounts-new)
    preview=adjustment_preview(db,actor,inv.id,str(new),p.get('discount_total_ngn'),p.get('rounding_basis'))
    if p.get('preview')!=preview:
        raise DomainError('preview_changed','Financial impact changed',409,preview)
    if not str(p.get('reason','')).strip(): raise DomainError('reason_required','Adjustment reason required')
    db.add(InvoiceAdjustment(invoice_id=inv.id,old_amount=inv.amount_ngn,new_amount=new,reason=p['reason'],actor_id=actor.id))
    if discounts!=old_discounts:
        db.add(Discount(invoice_id=inv.id,amount_ngn=discounts-old_discounts,reason='Price adjustment: '+p['reason'],actor_id=actor.id)); db.flush()
    if excess: unallocate(db,actor,inv,excess,'invoice_adjustment')
    inv.amount_ngn=new; inv.version+=1
    inv.rounding_adjustment_ngn=ZERO;rounding_basis=None
    if 'rounding_basis' in p:
        from .rounding import validate_basis
        inv.rounding_adjustment_ngn,rounding_basis=validate_basis(db,p['rounding_basis'],new,invoice=inv,payload=p)
    # Original typed components stay historical in the audit; changed totals require
    # explicit new components or mark this as a manual total, never stale components.
    inv.charge_usd=None; inv.charge_ngn=None; inv.ngn_per_usd=None
    if any(key in p for key in ('charge_usd','charge_ngn','ngn_per_usd')):
        usd=decimal(p.get('charge_usd','0')); ngn=decimal(p.get('charge_ngn','0'))
        rate=decimal(p['ngn_per_usd'],6,True) if usd else None
        calculated=settle(usd,'USD',{'direction':'NGN_PER_USD','rate':str(rate)})+ngn if usd else ngn
        if calculated+inv.rounding_adjustment_ngn!=new:raise DomainError('charge_mismatch','Updated typed charges and validated rounding differ from total')
        inv.charge_usd=usd; inv.charge_ngn=ngn; inv.ngn_per_usd=rate
    if 'pricing' in p: inv.pricing=p['pricing']
    if rounding_basis:
        from .rounding import save_basis
        save_basis(db,actor,inv,rounding_basis)
    invalidate(db,invoice_id=inv.id)
    after=invoice_json(db,inv); audit(db,actor,'invoice.adjusted','invoice',inv.id,before,after)
    return after|{'impact':preview}


def adjustment_preview(db,actor,identity,amount_ngn,discount_total_ngn=None,rounding_basis=None):
    inv=locked(db,Invoice,identity); require(actor,'payables.manage' if inv.kind=='AP' else 'finance.adjust',inv.warehouse_id); new=decimal(amount_ngn)
    allocated=total(db,Allocation,'amount_ngn',invoice_id=inv.id); old_discounts=total(db,Discount,'amount_ngn',invoice_id=inv.id)
    discounts=decimal(discount_total_ngn) if discount_total_ngn is not None else old_discounts
    payment_ids=set(db.scalars(select(Allocation.payment_id).where(Allocation.invoice_id==inv.id)))
    payments=list(db.scalars(select(Payment).where(Payment.id.in_(payment_ids)))) if payment_ids else []
    for payment in payments:payment_access(db,actor,payment,'payables.manage' if inv.kind=='AP' else 'finance.adjust')
    raw_cash=sum((p.amount_ngn for p in payments if p.approval!='opening'),ZERO)
    historical_credit=sum((p.amount_ngn for p in payments if p.approval=='opening'),ZERO)
    rounding=ZERO
    if rounding_basis is not None:
        from .rounding import validate_basis
        rounding,_=validate_basis(db,rounding_basis,new,invoice=inv)
    return json_value({'version':inv.version,'old_ngn':inv.amount_ngn,'new_ngn':new,'old_rounding_adjustment_ngn':inv.rounding_adjustment_ngn,'new_rounding_adjustment_ngn':rounding,'rounding_basis':rounding_basis,'original_payment_cash_ngn':raw_cash,'historical_credit_ngn':historical_credit,
        'allocated_ngn':allocated,'old_discount_ngn':old_discounts,'discount_ngn':discounts,'return_to_balance_ngn':max(ZERO,allocated+discounts-new),
        'outstanding_ngn':max(ZERO,new-discounts-allocated),'invalidate_unexecuted_instructions':True})


@command('invoice.discount','id expected_version amount_ngn reason waiver')
def add_discount(db,actor,p):
    inv=access(db,actor,Invoice,p['id'],'finance.discount'); check_version(inv,p.get('expected_version'))
    if inv.kind!='AR':raise DomainError('invalid_kind','Customer discount applies to receivables only')
    amount=decimal(p['amount_ngn'],positive=True)
    due=inv.amount_ngn-total(db,Discount,'amount_ngn',invoice_id=inv.id)-total(db,Allocation,'amount_ngn',invoice_id=inv.id)
    if amount>due or (p.get('waiver') and (amount!=due or amount>5000)):
        raise DomainError('invalid_discount','Discount exceeds outstanding or permitted tail waiver')
    db.add(Discount(invoice_id=inv.id,amount_ngn=amount,reason=p.get('reason'),actor_id=actor.id)); inv.version+=1
    invalidate(db,invoice_id=inv.id); db.flush(); return invoice_json(db,inv)


@command('payment.reverse','id expected_version reason claim_resolution confirmed')
def reverse_payment(db,actor,p):
    payment=access(db,actor,Payment,p['id'],'finance.reverse'); check_version(payment,p.get('expected_version'))
    if payment.state!='posted' or p.get('confirmed') is not True or not p.get('reason'):
        raise DomainError('confirmation_required','Explicit posted-payment reversal confirmation and reason required')
    for iid in sorted(set(db.scalars(select(Allocation.invoice_id).where(Allocation.payment_id==payment.id)))):
        inv=locked(db,Invoice,iid); amount=total(db,Allocation,'amount_ngn',invoice_id=iid,payment_id=payment.id)
        if amount: unallocate(db,actor,inv,amount,p['reason'],payment.id)
        inv.version+=1; invalidate(db,invoice_id=iid,payment_id=payment.id,kind='payment_reversed')
    claim=db.scalar(select(BankClaim).where(BankClaim.payment_id==payment.id,BankClaim.state!='released').with_for_update())
    if claim:
        resolution=p.get('claim_resolution','disputed')
        if resolution not in ('disputed','released'): raise DomainError('invalid_resolution','Choose disputed or explicitly released')
        if resolution=='released': require(actor,'bank.release',payment.warehouse_id)
        claim.state=resolution; claim.version+=1; payment.bank_state=resolution
    payment.state='reversed'; payment.version+=1
    return payment_json(db,payment)


@command('payable.pay','party_id warehouse_id scope amount currency fx paid_on period source_key allocations')
def pay_payable(db,actor,p):
    if not p.get('period'): raise DomainError('period_required','Choose billed party and period')
    payment=new_payment(db,actor,p,direction='out',state='posted')
    payment.approved_at=utcnow(); payment.approved_by=actor.id; payment.approval='manual'
    if p.get('allocations'): allocate(db,actor,payment,p['allocations'])
    return payment_json(db,payment)


@command('opening.create','party_id warehouse_id scope amount_ngn kind as_of source')
def create_opening_balance(db,actor,p):
    mode=party_scope(db,actor,p,'finance.opening'); kind=p['kind']; value=decimal(p['amount_ngn'],positive=True)
    if kind not in ('debt','credit') or not p['source']: raise DomainError('invalid_opening','Specify credit/debt and original source')
    obj=OpeningBalance(party_id=p['party_id'],warehouse_id=p['warehouse_id'],scope=mode,kind=kind,amount_ngn=value,as_of=day(p['as_of']),source=p['source'])
    if kind=='debt':
        inv=Invoice(party_id=p['party_id'],warehouse_id=p['warehouse_id'],scope=mode,kind='AR',reference='Opening balance',amount_ngn=value,uploaded_on=obj.as_of,due_on=obj.as_of)
        db.add(inv); db.flush(); obj.invoice_id=inv.id
    else:
        payment=Payment(party_id=p['party_id'],warehouse_id=p['warehouse_id'],scope=mode,direction='in',amount=None,currency=None,amount_ngn=value,fx_direction='HISTORICAL_UNKNOWN',state='posted',approval='opening',paid_on=None,approved_at=None)
        db.add(payment); db.flush(); obj.payment_id=payment.id
    db.add(obj); db.flush(); return record(obj)


def totals(db,actor,warehouse_id,mode='live',on=None):
    require(actor,'finance.read',warehouse_id); mode=scope(mode)
    zone=ZoneInfo(db.info.get('settings').business_timezone if db.info.get('settings') else 'Asia/Shanghai')
    selected=day(on) if on else datetime.now(zone).date()
    start=datetime.combine(selected,datetime.min.time(),zone); end=start+timedelta(days=1)
    all_rows=select(Payment).where(Payment.warehouse_id==warehouse_id,Payment.scope==mode,Payment.direction=='in',Payment.state=='posted')
    base=visible_payments(all_rows,actor)
    current=base.where(Payment.approved_at>=start,Payment.approved_at<end)
    rows=list(db.scalars(current.order_by(Payment.id).limit(50)))
    amount=db.scalar(current.with_only_columns(func.coalesce(func.sum(Payment.amount_ngn),0))).quantize(Decimal('.01'))
    count=db.scalar(select(func.count()).select_from(current.subquery()))
    unknown=db.scalar(select(func.count()).select_from(base.where(Payment.approved_at.is_(None)).subquery()))
    all_count=db.scalar(select(func.count()).select_from(all_rows.where(Payment.approved_at>=start,Payment.approved_at<end).subquery()))
    return json_value({'day':selected,'scope':mode,'today_ngn':amount,'payment_count':count,'scope_limited':all_count!=count,'unknown_approval_count':unknown,'payments':[payment_json(db,p) for p in rows], 'next':rows[-1].id if len(rows)==50 else None})


def payable_period(db,actor,warehouse_id,party_id,period,mode='live'):
    require(actor,'payables.read',warehouse_id); scope(mode)
    party_member(db,party_id,warehouse_id)
    invoices=list(db.scalars(select(Invoice).where(Invoice.party_id==party_id,Invoice.scope==mode,Invoice.kind=='AP',Invoice.period==period)))
    payments=list(db.scalars(select(Payment).where(Payment.party_id==party_id,Payment.scope==mode,Payment.direction=='out',Payment.period==period,Payment.state=='posted')))
    for invoice in invoices:require(actor,'payables.read',invoice.warehouse_id)
    for payment in payments:payment_access(db,actor,payment,'payables.read')
    billed=sum((i.amount_ngn for i in invoices),ZERO);paid=sum((p.amount_ngn for p in payments),ZERO)
    discounts=sum((total(db,Discount,'amount_ngn',invoice_id=i.id) for i in invoices),ZERO)
    return json_value({'party_id':party_id,'period':period,'billed_ngn':billed,'discount_ngn':discounts,'paid_ngn':paid,'balance_ngn':billed-discounts-paid,
        'warehouse_ids':sorted({warehouse_id}|{i.warehouse_id for i in invoices}|{p.warehouse_id for p in payments})})
