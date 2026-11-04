"""Immutable reviewed charge lines and bounded physical-batch allocation."""
from decimal import Decimal,ROUND_HALF_UP
from uuid import uuid5,NAMESPACE_URL
from sqlalchemy import select,func
from app.common import DomainError,json_value
from app.models import CargoChargeComponent
from app.finance.service import decimal,record
from app.cargo.commands import fields,enum

CENT=Decimal('.01')
UNITS={'PCS':'pieces','KG':'kg','CBM':'cbm'}
LINE_FIELDS='component currency unit amount source'


def normalize(lines):
    if not isinstance(lines,list) or not 1<=len(lines)<=100:raise DomainError('charge_components','Provide 1..100 reviewed source charge lines')
    result=[]
    for line in lines:
        fields(line,LINE_FIELDS)
        if set(line)!=set(LINE_FIELDS.split()):raise DomainError('charge_components','Every component needs kind, currency, unit, amount and source')
        if not isinstance(line['source'],str) or not line['source'].strip():raise DomainError('charge_source','Explicit component source required')
        result.append(dict(component=enum(line['component'],('freight','clearing'),'component'),currency=enum(line['currency'],('USD','NGN'),'currency'),unit=enum(line['unit'],tuple(UNITS),'unit'),amount=decimal(line['amount']) if line['amount'] is not None else None,source=line['source']))
    return result


def totals(lines):
    return tuple(None if any(c['amount'] is None for c in lines if c['currency']==currency) else sum((Decimal(c['amount']) for c in lines if c['currency']==currency),Decimal('0.00')) for currency in ('USD','NGN'))


def components(db,t):
    if hasattr(t,'_charge_components'):return t._charge_components
    version=db.scalar(select(func.max(CargoChargeComponent.ticket_version)).where(CargoChargeComponent.ticket_id==t.id,CargoChargeComponent.ticket_version<=t.version))
    return [{key:record(c)[key] for key in ('id','version','ticket_version',*LINE_FIELDS.split())} for c in db.scalars(select(CargoChargeComponent).where(CargoChargeComponent.ticket_id==t.id,CargoChargeComponent.ticket_version==version).order_by(CargoChargeComponent.position))] if version else []


def quote_lines(db,q):
    if hasattr(q,'_charge_components'):return q._charge_components
    return [record(c) for c in db.scalars(select(CargoChargeComponent).where(CargoChargeComponent.quote_id==q.id).order_by(CargoChargeComponent.position))]


def revise(db,t,p,quote=None,persist=True):
    previous=components(db,t)
    if 'charge_components' in p:
        lines=normalize(p['charge_components'])
        for key,total in zip(('freight_usd','clearing_ngn'),totals(lines)):
            if key in p and getattr(t,key)!=total:raise DomainError('component_total','Component sum must match reviewed original currency total')
            setattr(t,key,total)
    elif quote is not None:
        lines=[{k:c[k] for k in LINE_FIELDS.split()} for c in quote_lines(db,quote)]
    elif any(k in p for k in ('freight_usd','clearing_ngn')) and previous:
        lines=[{k:c[k] for k in LINE_FIELDS.split()} for c in previous]
        for key,currency in [('freight_usd','USD'),('clearing_ngn','NGN')]:
            if key not in p:continue
            selected=[c for c in lines if c['currency']==currency];new=getattr(t,key)
            old=totals(selected)[0 if currency=='USD' else 1]
            if new==old:continue
            if len(selected)==1:selected[0]['amount']=new
            elif not selected and new==0:continue
            else:raise DomainError('component_review','Changed multi-line or missing currency total requires reviewed charge_components')
    else:return
    lines=normalize([{k:c[k] for k in LINE_FIELDS.split()} for c in lines])
    if persist:
        for index,line in enumerate(lines):db.add(CargoChargeComponent(id=str(uuid5(NAMESPACE_URL,f'cargo-component:{t.id}:{t.version}:{index}')),ticket_id=t.id,ticket_version=t.version,position=index,**line))
        db.flush()
    else:
        # Deterministic revision IDs make reviewed preview and committed proof identical.
        # IDs never replace the current source-value and version validation.
        t._charge_components=[dict(line,id=str(uuid5(NAMESPACE_URL,f'cargo-component:{t.id}:{t.version}:{i}')),version=1,ticket_version=t.version) for i,line in enumerate(lines)]


def ready(db,t):
    if t.pricing_authority=='review':return False
    lines=components(db,t)
    if not lines:return False
    values=totals(lines)
    return None not in values and values==(t.freight_usd,t.clearing_ngn)


def projection(db,t,rows):
    lines=components(db,t);priced=ready(db,t) and (not t.freight_usd or t.ngn_per_usd)
    cumulative={k:Decimal(0) for k in UNITS.values()};used=[Decimal('0.00') for c in lines];result=[]
    for a in rows:
        for k in cumulative:cumulative[k]+=Decimal(getattr(a,k))
        complete=all(cumulative[k]==getattr(t,k) for k in cumulative)
        parts=[];usd=ngn=None
        if priced:
            for i,line in enumerate(lines):
                total=Decimal(line['amount']);unit=UNITS[line['unit']];denominator=getattr(t,unit)
                if total==0:amount=Decimal('0.00')
                elif denominator is None or denominator<=0:raise DomainError('arrival_basis','Positive charge requires nonzero reviewed unit quantity')
                elif complete:amount=total-used[i]
                else:
                    share=(total*Decimal(getattr(a,unit))/denominator).quantize(CENT,rounding=ROUND_HALF_UP)
                    ceiling=(total*cumulative[unit]/denominator).quantize(CENT,rounding=ROUND_HALF_UP)
                    amount=min(share,total-used[i],max(Decimal(0),ceiling-used[i]))
                used[i]+=amount
                parts.append(dict(component_id=line['id'],component_version=line['version'],ticket_version=line.get('ticket_version'),component=line['component'],currency=line['currency'],unit=line['unit'],source=line['source'],original_amount=total,amount=amount))
            usd=sum((c['amount'] for c in parts if c['currency']=='USD'),Decimal('0.00'));ngn=sum((c['amount'] for c in parts if c['currency']=='NGN'),Decimal('0.00'))
        result.append((a,usd,ngn,json_value(parts)))
    return result


def settlement_control_and_quota(db,t,quantities):
    control=quota=Decimal('0.00')
    for line in components(db,t):
        total=Decimal(line['amount']);unit=UNITS[line['unit']];denominator=getattr(t,unit)
        rate=Decimal(t.ngn_per_usd) if line['currency']=='USD' and total else Decimal(1)
        control+=(total*rate).quantize(CENT,rounding=ROUND_HALF_UP)
        if total:
            if denominator is None or denominator<=0:raise DomainError('arrival_basis','Positive charge requires nonzero reviewed unit quantity')
            quota+=(total*rate*quantities[unit]/denominator).quantize(CENT,rounding=ROUND_HALF_UP)
    return control,quota
