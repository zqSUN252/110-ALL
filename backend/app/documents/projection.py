from app.packing.quantities import known_sum
"""One formal packing-list projection for internal and verified-owner documents."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.common import json_value,DomainError
from app.models import Ticket,Receipt,PackingDocument,Shipment,Warehouse,Party,Invoice,QuoteSnapshot,AppSetting,PriceCard,Arrival
from app.finance.service import record,invoice_json
from app.packing.allocation import components,quote_lines

PENDING='TO BE CONFIRMED'


def today(db):
    return datetime.now(ZoneInfo(db.info['settings'].business_timezone)).date()


def aggregate(rows,key):
    values=[r.get(key) for r in rows];known=[Decimal(str(v)) for v in values if v is not None]
    pending=len(values)-len(known)
    return {'value':str(sum(known,Decimal(0))) if not pending else None,'known_subtotal':str(sum(known,Decimal(0))),'pending_count':pending}


FINANCIAL_KEYS=('invoice_total_ngn','allocated_ngn','received_total_ngn','historical_received_ngn','outstanding_ngn','discount_ngn','rounding_adjustment_ngn')


def financial_totals(rows):
    """One invoice is one financial control even when many physical rows refer to it."""
    unique=list({r['invoice_id'] or r['id']:r for r in rows}.values())
    return {key:aggregate(unique,key) for key in FINANCIAL_KEYS}


def shown(value):return PENDING if value is None or value=='' else str(value)


def total_text(total):
    return total['value'] if total['value'] is not None else f"{total['known_subtotal']} known; {total['pending_count']} pending"


def profile(db):
    row=db.scalar(select(AppSetting).where(AppSetting.key=='documents.profile'))
    allowed={'china_contacts','packing_list_contact','customer_care_contact','tracking_domain','bank_name','account_holder','account_number','public_company_name','public_hotline','public_openshipment','public_address','public_website'}
    return {k:v for k,v in (row.value if row else {}).items() if k in allowed}


def row(db,obj):
    ticket=isinstance(obj,Ticket);warehouse=db.get(Warehouse,obj.warehouse_id)
    destination=db.get(Warehouse,obj.destination_warehouse_id) if obj.destination_warehouse_id else None
    party=db.get(Party,obj.party_id) if obj.party_id else None
    d=db.get(PackingDocument,obj.document_id) if ticket else None;s=db.get(Shipment,d.shipment_id) if d else None
    quote=db.get(QuoteSnapshot,obj.quote_id) if obj.quote_id and (not ticket or obj.pricing_authority=='quote') else None
    lines=components(db,obj) if ticket and obj.pricing_authority!='review' else [] if ticket else quote_lines(db,quote) if quote else []
    charges=[]
    for c in lines:
        qty=getattr(obj,{'PCS':'pieces','KG':'kg','CBM':'cbm'}[c['unit']]);amount=Decimal(str(c['amount'])) if c['amount'] is not None else None
        charges.append({'component':c['component'],'currency':c['currency'],'unit':c['unit'],'amount':amount,'quantity':qty,'unit_price':amount/Decimal(qty) if amount is not None and qty else None})
    inv=db.get(Invoice,obj.invoice_id) if ticket and obj.invoice_id else None
    invoice=invoice_json(db,inv) if inv else None
    result={'source_type':'ticket' if ticket else 'receipt','id':obj.id,'version':obj.version,'warehouse_id':obj.warehouse_id,'party_id':obj.party_id,
        'document_id':d.id if d else None,'document_version':d.version if d else None,'shipment_id':s.id if s else None,'shipment_version':s.version if s else None,
        'receipt':obj.receipt if ticket else obj.reference,'mark':obj.mark if ticket else party.name if party else None,'phone':obj.phone_normalized if ticket and obj.phone_confirmed else obj.contact_raw if ticket else obj.customer_phone,
        'item_zh':obj.item_zh,'item_en':obj.item_en,'received_on':obj.received_on,'transport':obj.transport,'origin':obj.origin,'goods_class':obj.goods_class,'destination':obj.destination,'destination_warehouse_id':obj.destination_warehouse_id,
        'warehouse':destination.name if destination else None,'address':destination.address if destination else obj.address if ticket else None,'destination_contact':destination.contact if destination else None,
        'origin_warehouse':warehouse.name,'origin_contact':warehouse.contact,'container':s.reference if s else None,'pieces':obj.pieces,'kg':obj.kg,'cbm':obj.cbm,
        'freight_usd':obj.freight_usd if ticket else quote.charge_usd if quote else None,'clearing_ngn':obj.clearing_ngn if ticket else quote.charge_ngn if quote else None,
        'ngn_per_usd':obj.ngn_per_usd if ticket else quote.ngn_per_usd if quote else None,'charges':charges,'pricing_authority':obj.pricing_authority if ticket else 'quote' if quote else 'pending',
        'invoice_id':inv.id if inv else None,'invoice_reference':inv.reference if inv else None,'invoice_version':inv.version if inv else None,'invoice_total_ngn':inv.amount_ngn if inv else None,'rounding_adjustment_ngn':inv.rounding_adjustment_ngn if inv else None,
        'allocated_ngn':invoice['allocated_ngn'] if invoice else None,'historical_received_ngn':invoice['historical_received_ngn'] if invoice else None,
        'received_total_ngn':Decimal(invoice['allocated_ngn'])+Decimal(invoice['historical_received_ngn']) if invoice else None,
        'historical_bank_state':invoice['historical_bank_state'] if invoice else None,'discount_ngn':invoice['discount_ngn'] if invoice else None,'outstanding_ngn':invoice['outstanding_ngn'] if invoice else None}
    if ticket:
        if obj.billing_group_id:
            from app.models import NativeBillingGroup
            group=db.get(NativeBillingGroup,obj.billing_group_id)
            result['billing_group_id']=group.id;result['billing_group_version']=group.version
        from app.packing.allocation import projection as allocated_arrivals
        from app.tracking.service import warehouse_arrivals,warehouse_basis
        actual=list(db.scalars(select(Arrival).where(Arrival.ticket_id==obj.id).order_by(Arrival.created_at,Arrival.id)))
        unresolved=any(c['amount'] is None or (Decimal(str(c['amount']))>0 and not getattr(obj,{'PCS':'pieces','KG':'kg','CBM':'cbm'}[c['unit']])) for c in charges)
        batches=[(a,None,None,[]) for a in actual] if unresolved else allocated_arrivals(db,obj,actual)
        confirmed=warehouse_arrivals(db,obj)
        quantities={k:known_sum(getattr(a,k) for a in confirmed) for k in ('pieces','kg','cbm')}
        billing_quantities={k:known_sum(getattr(a,k) for a in actual) for k in ('pieces','kg','cbm')}
        actual_charges=[]
        for index,c in enumerate(charges):
            values=[parts[index]['amount'] if len(parts)>index else None for a,u,n,parts in batches]
            amount=sum((Decimal(str(v)) for v in values),Decimal(0)) if values and None not in values else None
            actual_charges.append(dict(c,amount=amount,quantity=billing_quantities[{'PCS':'pieces','KG':'kg','CBM':'cbm'}[c['unit']]]))
        result['arrival_view']=dict(quantities,charges=actual_charges,freight_usd=inv.charge_usd if inv else None,clearing_ngn=inv.charge_ngn if inv else None)
        result['warehouse_confirmation']=warehouse_basis(db,obj)
    return json_value(result)


def packing_projection(db,objects):
    rows=[row(db,o) for o in objects]
    totals={key:aggregate(rows,key) for key in ('pieces','kg','cbm','freight_usd','clearing_ngn')}|financial_totals(rows)
    return {'template_version':'packing-244-535-v1','rows':rows,'totals':totals,'profile':profile(db),'generated_on':today(db).isoformat()}


def rates(db,rows):
    on=today(db);destinations={r['destination'] for r in rows if r['destination']};unknown=any(not r['destination'] for r in rows)
    cards=list(db.scalars(select(PriceCard).where(PriceCard.active.is_(True),PriceCard.valid_from<=on,PriceCard.valid_to>=on).order_by(PriceCard.destination,PriceCard.transport,PriceCard.origin,PriceCard.id)))
    if not unknown:cards=[c for c in cards if c.destination in destinations and any(r['transport']==c.transport and r['origin']==c.origin and (not r.get('goods_class') or r['goods_class']==c.goods_class) for r in rows)]
    lines=[]
    for c in cards:
        lines.append(f"{c.destination} | {c.transport} / {c.origin} / {c.goods_class} | {c.currency} {c.unit_price:,.2f} PER {c.unit}"+(f" | Clearing NGN {c.clearing_per_cbm_ngn:,.2f} PER CBM" if c.clearing_per_cbm_ngn is not None else '')+f" | {c.valid_from} — {c.valid_to}")
    selection={'on':on.isoformat(),'mode':'full_reference' if unknown else 'applicable',
        'sources':[{key:r.get(key) for key in ('source_type','id','destination','transport','origin','goods_class')} for r in rows]}
    return {'selection':selection,'heading':PENDING if unknown else ', '.join(sorted(destinations)),'lines':lines or ['No applicable effective price card — TO BE CONFIRMED'],'cards':[record(c) for c in cards]}
