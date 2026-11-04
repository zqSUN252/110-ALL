from decimal import Decimal,ROUND_HALF_UP
from sqlalchemy import select
from app.common import DomainError,require,check_version
from app.models import PriceCard,QuoteSnapshot,Receipt
from app.finance.service import decimal,day,record,settle
from app.cargo.commands import command,enum,port,object_access,fields,bump

CARD_FIELDS='transport origin goods_class destination unit currency unit_price clearing_per_cbm_ngn valid_from valid_to source active'


def card_values(p):
    r={k:v for k,v in p.items() if k in CARD_FIELDS.split()}
    enum(r['transport'],('SEA','AIR','EXPRESS'),'transport');enum(r['origin'],('GZ','HK'),'origin')
    enum(r['currency'],('USD','NGN'),'currency');enum(r['unit'],('KG','PCS','CBM'),'unit')
    r['destination']=port(r['destination']);r['unit_price']=decimal(r['unit_price'])
    r['valid_from']=day(r['valid_from']);r['valid_to']=day(r['valid_to'])
    if r['valid_from']>r['valid_to'] or not r['destination'] or not str(r['source']).strip():raise DomainError('card_basis','Explicit destination, source and valid period required')
    if r.get('clearing_per_cbm_ngn') is not None:
        r['clearing_per_cbm_ngn']=decimal(r['clearing_per_cbm_ngn'])
        if r['transport']!='SEA' and r['clearing_per_cbm_ngn']!=0:raise DomainError('all_in','AIR/EXPRESS are all-in, no extra clearing')
    if r['transport'] in ('AIR','EXPRESS') and (r['unit']!='KG' or r['currency']!='USD'):
        raise DomainError('default_air_unit','Air price cards use USD/KG; explicit ticket overrides support NGN/PCS')
    return r


@command('price_card.create',CARD_FIELDS,'pricing.manage')
def create_card(db,actor,p):
    c=PriceCard(**card_values(p));db.add(c);db.flush();return record(c)


@command('price_card.update','id expected_version '+CARD_FIELDS,'pricing.manage',PriceCard)
def update_card(db,actor,p):
    c=object_access(db,actor,PriceCard,p['id'],'pricing.manage');check_version(c,p.get('expected_version'))
    data={k:getattr(c,k) for k in CARD_FIELDS.split()}|{k:v for k,v in p.items() if k in CARD_FIELDS.split()}
    for k,v in card_values(data).items():setattr(c,k,v)
    c.version+=1;db.flush();return record(c)


def list_cards(db,actor,on=None,destination=None,limit=100,offset=0):
    require(actor,'pricing.read')
    if not 1<=limit<=200 or offset<0:raise DomainError('pagination','Invalid pagination')
    q=select(PriceCard)
    if on:q=q.where(PriceCard.valid_from<=day(on),PriceCard.valid_to>=day(on),PriceCard.active.is_(True))
    if destination:q=q.where(PriceCard.destination==port(destination))
    return [record(c) for c in db.scalars(q.order_by(PriceCard.transport,PriceCard.origin,PriceCard.destination,PriceCard.valid_from.desc(),PriceCard.id).limit(limit).offset(offset))]


def build_quote(db,r,manual=None,fx=None,persist=True):
    fx=fx or {};card=None
    if manual is None:
        if not all((r.origin,r.goods_class,r.destination,r.received_on)):return None
        cards=list(db.scalars(select(PriceCard).where(PriceCard.transport==r.transport,PriceCard.origin==r.origin,PriceCard.goods_class==r.goods_class,PriceCard.destination==r.destination,PriceCard.valid_from<=r.received_on,PriceCard.valid_to>=r.received_on,PriceCard.active.is_(True))))
        if not cards:return None
        if len(cards)!=1:raise DomainError('ambiguous_price','Multiple valid cards; select explicit manual price with source',409)
        card=cards[0];basis=record(card);unit=card.unit;currency=card.currency;price=card.unit_price
    else:
        fields(manual,'unit_price currency unit source');unit=enum(manual['unit'],('KG','PCS','CBM'),'unit');currency=enum(manual['currency'],('USD','NGN'),'currency');price=decimal(manual['unit_price']);basis=manual
    raw_quantity={'KG':r.kg,'PCS':r.pieces,'CBM':r.cbm}[unit]
    if raw_quantity is None:return None
    quantity=Decimal(str(raw_quantity));amount=(price*quantity).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    usd=amount if currency=='USD' else Decimal('0.00');ngn=amount if currency=='NGN' else Decimal('0.00')
    if card and card.transport=='SEA' and card.clearing_per_cbm_ngn is not None:ngn+=(card.clearing_per_cbm_ngn*r.cbm).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    rate=decimal(fx['rate'],6,True) if usd and fx.get('direction')=='NGN_PER_USD' else None
    if fx and usd:settle(usd,'USD',fx)
    total=(settle(usd,'USD',{'direction':'NGN_PER_USD','rate':str(rate)})+ngn) if rate else ngn if not usd else None
    q=QuoteSnapshot(warehouse_id=r.warehouse_id,card_id=card.id if card else None,manual=manual is not None,unit=unit,currency=currency,unit_price=price,quantity=quantity,charge_usd=usd,charge_ngn=ngn,ngn_per_usd=rate,amount_ngn=total,basis={'selection':basis,'transport':r.transport,'origin':r.origin,'goods_class':r.goods_class,'destination':r.destination,'received_on':str(r.received_on)})
    if persist:db.add(q);db.flush()
    return q


def refresh_quote(db,r):
    old=db.get(QuoteSnapshot,r.quote_id) if r.quote_id else None
    manual={'unit_price':str(old.unit_price),'currency':old.currency,'unit':old.unit,'source':'Retained explicit override'} if old and old.manual else None
    fx={'direction':'NGN_PER_USD','rate':str(old.ngn_per_usd)} if old and old.ngn_per_usd else None
    q=build_quote(db,r,manual,fx);r.quote_id=q.id if q else None
    return q


@command('receipt.quote','id expected_version manual fx','pricing.manage',Receipt)
def quote_receipt(db,actor,p):
    r=object_access(db,actor,Receipt,p['id'],'pricing.manage');check_version(r,p.get('expected_version'))
    q=build_quote(db,r,p.get('manual'),p.get('fx'));r.quote_id=q.id if q else None;bump(r);db.flush()
    from app.cargo.service import receipt_json
    return receipt_json(db,r)
