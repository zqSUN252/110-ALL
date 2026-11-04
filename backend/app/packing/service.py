from app.packing.quantities import known_sum,nonkg_sea,complete as quantity_complete
"""Documents own memberships; stable tickets own arrivals, invoices and attachment links."""
from decimal import Decimal,ROUND_HALF_UP
from sqlalchemy import select,func
from app.common import DomainError,require,check_version,json_value
from app.models import (PackingDocument,Ticket,Shipment,Arrival,Invoice,QuoteSnapshot,TicketAttachment,TicketReview,Attachment,DocumentRevision,CargoSourceRow,ImportBatch)
from app.finance import service as finance
from . import allocation,pricing
from app.finance.access import payment_access
from app.cargo.commands import command,object_access,preview_access,native_link,native_change,member,enum,port,integer,fields,bump,visible

TICKET_FIELDS='party_id native_receipt_id receipt mark item_zh item_en contact_raw phone_normalized phone_confirmed address destination destination_warehouse_id transport origin goods_class received_on pieces kg cbm freight_usd clearing_ngn ngn_per_usd charge_components'


def ticket_values(p):
    out={k:v for k,v in p.items() if k in TICKET_FIELDS.split() and k!='charge_components'}
    for key in ('kg','cbm','ngn_per_usd'):
        if key in out:out[key]=finance.decimal(out[key],6,key=='ngn_per_usd') if out[key] is not None else None
    for key in ('freight_usd','clearing_ngn'):
        if key in out:out[key]=finance.decimal(out[key]) if out[key] is not None else None
    if 'pieces' in out:out['pieces']=integer(out['pieces']) if out['pieces'] is not None else None
    if 'transport' in out:enum(out['transport'],('SEA','AIR','EXPRESS','WAREHOUSE'),'transport')
    if out.get('origin'):enum(out['origin'],('GZ','HK'),'origin')
    if 'destination' in out:out['destination']=port(out['destination'])
    if 'received_on' in out:out['received_on']=finance.day(out['received_on']) if out['received_on'] else None
    if 'receipt' in out and out['receipt'] is not None and (not isinstance(out['receipt'],str) or not out['receipt'].strip()):raise DomainError('receipt_required','Original receipt text or explicit unknown required')
    return out


def ticket_json(db,t):
    from app.models import PhysicalRelease
    pickup=db.scalar(select(func.max(PhysicalRelease.occurred_at)).where(PhysicalRelease.ticket_id==t.id))
    arrivals=[finance.record(a)|{'effective_freight_usd':str(usd) if usd is not None else None,'effective_clearing_ngn':str(ngn) if ngn is not None else None} for a,usd,ngn in arrival_projection(db,t)]
    observed_kg=known_sum(Decimal(a['kg']) if a['kg'] is not None else None for a in arrivals)
    return finance.record(t)|{'charge_components':allocation.components(db,t),'arrivals':arrivals,'arrived':{'pieces':sum(a['pieces'] for a in arrivals),'kg':f'{observed_kg:.6f}' if observed_kg is not None else None,'cbm':f"{sum((Decimal(a['cbm']) for a in arrivals),Decimal(0)):.6f}"},
        'attachments':[finance.record(a) for a in db.scalars(select(TicketAttachment).where(TicketAttachment.ticket_id==t.id))],
        'quote_id':t.quote_id if t.pricing_authority=='quote' else None,'quote':finance.record(db.get(QuoteSnapshot,t.quote_id)) if t.quote_id and t.pricing_authority=='quote' else None,'historical_quote_id':t.quote_id if t.pricing_authority!='quote' else None,'actual_release_at':pickup.isoformat() if pickup else None}


def get_ticket(db,actor,identity):
    t=object_access(db,actor,Ticket,identity,'packing.read')
    if t.invoice_id:finance.get_invoice(db,actor,t.invoice_id)
    return ticket_json(db,t)


def document_json(db,d):
    return finance.record(d)|{'tickets':[ticket_json(db,t) for t in db.scalars(select(Ticket).where(Ticket.document_id==d.id).order_by(Ticket.position,Ticket.id))],
        'revisions':[logistics_history(finance.record(r)) for r in db.scalars(select(DocumentRevision).where(DocumentRevision.document_id==d.id).order_by(DocumentRevision.created_at,DocumentRevision.id))]}


def logistics_history(value):
    if isinstance(value,list):return [logistics_history(v) for v in value]
    if isinstance(value,dict):return {k:logistics_history(v) for k,v in value.items() if k not in ('finance_preview','financial_sources')}
    return value


def financial_sources(db,t):
    from app.models import Payment,Allocation
    from app.finance.access import payment_warehouses
    if not t.invoice_id:return None
    payments=list(db.scalars(select(Payment).join(Allocation,Allocation.payment_id==Payment.id).where(Allocation.invoice_id==t.invoice_id).distinct()))
    return {'invoice_id':t.invoice_id,'warehouse_ids':sorted({t.warehouse_id}|{w for p in payments for w in payment_warehouses(db,p)}),'payment_ids':[p.id for p in payments]}


def get_document_history(db,actor,identity):
    from app.models import Payment
    d=object_access(db,actor,PackingDocument,identity,'packing.read')
    revisions=list(db.scalars(select(DocumentRevision).where(DocumentRevision.document_id==d.id).order_by(DocumentRevision.created_at,DocumentRevision.id)))
    for revision in revisions:
        for change in revision.difference.get('changes',[]):
            if not change.get('finance_preview'):continue
            sources=change.get('financial_sources')
            if not sources:
                # Prior revisions still retain stable ticket IDs after split.
                t=finance.locked(db,Ticket,change['ticket_id']);sources=financial_sources(db,t)
            if not sources:raise DomainError('history_source','Protected historical sources require resolution',403)
            for warehouse in sources['warehouse_ids']:require(actor,'finance.read',warehouse)
            finance.get_invoice(db,actor,sources['invoice_id'])
            for payment_id in sources['payment_ids']:payment_access(db,actor,finance.locked(db,Payment,payment_id),'finance.read')
    return [finance.record(r) for r in revisions]


def get_document(db,actor,identity):
    d=object_access(db,actor,PackingDocument,identity,'packing.read')
    for t in db.scalars(select(Ticket).where(Ticket.document_id==d.id)):
        if t.invoice_id:finance.get_invoice(db,actor,t.invoice_id)
    return document_json(db,d)


@command('document.create','shipment_id reference uploaded_on','packing.manage')
def create_document(db,actor,p):
    s=object_access(db,actor,Shipment,p['shipment_id'],'packing.manage')
    d=PackingDocument(shipment_id=s.id,warehouse_id=s.warehouse_id,reference=p['reference'],uploaded_on=finance.day(p['uploaded_on']))
    db.add(d);db.flush();return document_json(db,d)


def new_ticket(db,actor,d,p,allow_unresolved=False):
    if d.state!='active':raise DomainError('document_void','Document is not active',409)
    member(db,actor,p.get('party_id'),d.warehouse_id,'packing.manage')
    s=db.get(Shipment,d.shipment_id);data=ticket_values(p)
    data.setdefault('transport',s.transport);data.setdefault('origin',s.origin)
    if data['transport']!=s.transport:raise DomainError('transport_mismatch','Ticket and shipment transport differ')
    if not allow_unresolved and any(data.get(k) is None for k in ('receipt','pieces','kg','cbm')):raise DomainError('quantities_missing','Explicit receipt, pieces, gross KG and CBM required; imported unknowns remain pending')
    native_link(db,actor,d.warehouse_id,data.get('native_receipt_id'),data.get('receipt'),data.get('party_id'),data.get('transport'))
    position=(db.scalar(select(func.max(Ticket.position)).where(Ticket.document_id==d.id)) or 0)+1
    t=Ticket(document_id=d.id,warehouse_id=d.warehouse_id,position=position,**data)
    db.add(t);db.flush()
    q=None
    if 'charge_components' not in p and t.freight_usd is None and t.clearing_ngn is None:
        from app.pricing.service import refresh_quote
        q=refresh_quote(db,t)
        if q:t.freight_usd=q.charge_usd;t.clearing_ngn=q.charge_ngn;t.ngn_per_usd=q.ngn_per_usd
    t.pricing_authority='quote' if q else 'totals' if 'charge_components' in p or t.freight_usd is not None or t.clearing_ngn is not None else 'pending'
    allocation.revise(db,t,p,q)
    d.version+=1;return t


@command('ticket.create','document_id '+TICKET_FIELDS,'packing.manage')
def create_ticket(db,actor,p):
    d=object_access(db,actor,PackingDocument,p['document_id'],'packing.manage');t=new_ticket(db,actor,d,p)
    db.flush();return ticket_json(db,t)


def charges(db,t):
    if t.freight_usd is None or t.clearing_ngn is None:return None
    usd=t.freight_usd;ngn=t.clearing_ngn
    if usd and not t.ngn_per_usd:return None
    return usd,ngn


def invoice_amount(usd,ngn,rate):
    return finance.settle(usd,'USD',{'direction':'NGN_PER_USD','rate':str(rate)})+ngn if usd else ngn


def financial_access(db,actor,t,adjust=True):
    if not t.invoice_id:return
    inv=finance.locked(db,Invoice,t.invoice_id)
    require(actor,'finance.read',inv.warehouse_id)
    require(actor,'finance.adjust' if adjust else 'finance.read',inv.warehouse_id)
    from app.models import Payment,Allocation
    for pay in db.scalars(select(Payment).join(Allocation,Allocation.payment_id==Payment.id).where(Allocation.invoice_id==inv.id).distinct()):
        payment_access(db,actor,pay,'finance.read');payment_access(db,actor,pay,'finance.adjust' if adjust else 'finance.read')


def arrival_projection(db,t):
    rows=list(db.scalars(select(Arrival).where(Arrival.ticket_id==t.id).order_by(Arrival.created_at,Arrival.id)))
    return [(a,usd,ngn) for a,usd,ngn,_ in allocation.projection(db,t,rows)]


def arrived_charges(db,t):
    projection=arrival_projection(db,t)
    if not projection or charges(db,t) is None or any(usd is None or ngn is None for _,usd,ngn in projection):return None
    return sum((usd for _,usd,_ in projection),Decimal('0.00')),sum((ngn for _,_,ngn in projection),Decimal('0.00'))


def sync_invoice(db,actor,t,p,automatic_arrival=False):
    from app.migration.service import ticket_legacy_bills
    if ticket_legacy_bills(db,t.id):return
    if t.pricing_authority=='review':return
    if automatic_arrival and t.invoice_id:
        financial_access(db,actor,t)
        historical=finance.locked(db,Invoice,t.invoice_id)
        if not (historical.pricing or {}).get('arrival_allocation') or not allocation.ready(db,t):
            return  # Historical AR, including pre-component allocation proofs, stays authoritative until review.
    values=arrived_charges(db,t)
    basis=None
    if values is not None:
        from app.finance.rounding import cargo_basis
        basis=cargo_basis(db,t)
    if t.invoice_id and values is None:
        if charges(db,t) is None:raise DomainError('existing_invoice_unpriced','Existing invoice cannot lose its price; provide reviewed replacement',409)
        values=charges(db,t)  # imported existing AR survives differing arrival history
    if values is None:return
    usd,ngn=values;amount=Decimal(basis['amount_ngn']) if basis else invoice_amount(usd,ngn,t.ngn_per_usd)
    if t.invoice_id:
        financial_access(db,actor,t);inv=finance.locked(db,Invoice,t.invoice_id)
        if automatic_arrival:
            previous=(inv.pricing or {}).get('arrival_allocation')
            # Imported/manual existing AR is authoritative historical evidence;
            # new arrival eligibility must never silently shrink or rebuild it.
            if previous is None:return
            if inv.amount_ngn!=Decimal(previous['amount_ngn']) or inv.rounding_adjustment_ngn!=Decimal(previous['rounding_adjustment_ngn']):raise DomainError('invoice_basis_changed','Invoice was edited independently; review cargo/financial difference before automatic arrival repricing',409)
        prior_basis=(inv.pricing or {}).get('arrival_allocation') or {}
        source_changed=basis is not None and any(prior_basis.get(key)!=basis.get(key) for key in ('components','source_quantities','ngn_per_usd','original_total_ngn','arrivals'))
        if source_changed or inv.amount_ngn!=amount or inv.charge_usd!=usd or inv.charge_ngn!=ngn or inv.rounding_adjustment_ngn!=(Decimal(basis['rounding_adjustment_ngn']) if basis else Decimal(0)):
            preview=finance.adjustment_preview(db,actor,inv.id,str(amount),p.get('discount_total_ngn'),basis) if automatic_arrival else p.get('finance_preview')
            payload=dict(id=inv.id,expected_version=inv.version,amount_ngn=str(amount),reason=p.get('reason') or 'Arrival share of original explicit cargo bill',preview=preview,charge_usd=str(usd),charge_ngn=str(ngn),idempotency_key=p['idempotency_key']+':invoice')
            if usd:payload['ngn_per_usd']=str(t.ngn_per_usd)
            if basis:payload['rounding_basis']=basis
            if p.get('discount_total_ngn') is not None:payload['discount_total_ngn']=p['discount_total_ngn']
            finance.adjust_invoice(db,actor,payload)
    else:
        if not t.party_id:raise DomainError('party_unresolved','Confirmed party membership required before creating AR')
        d=db.get(PackingDocument,t.document_id)
        payload=dict(warehouse_id=t.warehouse_id,party_id=t.party_id,reference=t.receipt,uploaded_on=d.uploaded_on.isoformat(),amount_ngn=str(amount),charge_usd=str(usd),charge_ngn=str(ngn),source_key='ticket:'+t.id,idempotency_key=p['idempotency_key']+':invoice')
        if usd:payload['ngn_per_usd']=str(t.ngn_per_usd)
        if basis:payload['rounding_basis']=basis
        t.invoice_id=finance.create_invoice(db,actor,payload)['id']


def edit_ticket(db,actor,t,p):
    from app.migration.service import guard_legacy_ticket
    guard_legacy_ticket(db,t.id,p)
    if t.state!='active':raise DomainError('ticket_void','Recover ticket before editing',409)
    if 'party_id' in p:
        member(db,actor,p['party_id'],t.warehouse_id,'packing.manage')
        if t.invoice_id and p['party_id']!=t.party_id:raise DomainError('financial_party','A billed ticket requires explicit financial ownership resolution',409)
    native_change(db,actor,t,p)
    before=pricing.snapshot(t)
    for k,v in ticket_values(p).items():setattr(t,k,v)
    if t.transport!=db.get(Shipment,db.get(PackingDocument,t.document_id).shipment_id).transport:raise DomainError('transport_mismatch','Ticket and shipment transport differ')
    existing=list(db.scalars(select(Arrival).where(Arrival.ticket_id==t.id)))
    if existing:
        for k in ('pieces','kg','cbm'):
            observed=known_sum(getattr(a,k) for a in existing)
            if getattr(t,k) is not None and (observed is None or observed>getattr(t,k)):
                raise DomainError('arrived_quantity','Existing unknown batches require explicit quantity reconciliation',409)
    q=pricing.apply(db,t,p,before)
    bump(t);allocation.revise(db,t,p,q)
    if pricing.requested(p) or allocation.ready(db,t):sync_invoice(db,actor,t,p)


def ticket_change_preview(db,actor,identity,changes):
    from sqlalchemy import text
    from types import SimpleNamespace
    from app.pricing.service import build_quote
    from app.finance.rounding import cargo_basis
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    t=preview_access(db,actor,Ticket,identity,'packing.manage');financial_access(db,actor,t)
    from app.migration.service import guard_legacy_ticket
    guard_legacy_ticket(db,t.id,changes)
    fields(changes,TICKET_FIELDS+' manual fx repricing reason discount_total_ngn')
    proposed=SimpleNamespace(**{c.name:getattr(t,c.name) for c in t.__table__.columns})
    native_change(db,actor,t,changes)
    for key,value in ticket_values(changes).items():setattr(proposed,key,value)
    proposed.version=t.version+1
    quote=pricing.apply(db,proposed,changes,pricing.snapshot(t),persist=False)
    allocation.revise(db,proposed,changes,quote,persist=False)
    impact=None
    metadata_only=bool(changes) and not pricing.requested(changes) and proposed.pricing_authority in ('totals','review')
    if t.invoice_id and not metadata_only:
        values=charges(db,proposed)
        if values is None:raise DomainError('existing_invoice_unpriced','Existing invoice requires an explicit replacement price',409)
        if db.scalar(select(Arrival.id).where(Arrival.ticket_id==t.id)):
            basis=cargo_basis(db,proposed);impact=finance.adjustment_preview(db,actor,t.invoice_id,basis['amount_ngn'],changes.get('discount_total_ngn'))
            impact.update(new_rounding_adjustment_ngn=basis['rounding_adjustment_ngn'],rounding_basis=basis)
        else:impact=finance.adjustment_preview(db,actor,t.invoice_id,str(invoice_amount(*values,proposed.ngn_per_usd)),changes.get('discount_total_ngn'))
    return {'id':t.id,'version':t.version,'warehouse_id':t.warehouse_id,'changes':json_value(changes),'pricing_authority':proposed.pricing_authority,'quote':finance.record(quote or db.get(QuoteSnapshot,proposed.quote_id)) if proposed.pricing_authority=='quote' and (quote or proposed.quote_id) else None,'historical_quote_id':proposed.quote_id if proposed.pricing_authority=='review' else None,'finance_preview':impact}


@command('ticket.update','id expected_version manual fx repricing finance_preview discount_total_ngn reason '+TICKET_FIELDS,'packing.manage',Ticket,financial=True)
def update_ticket(db,actor,p):
    t=preview_access(db,actor,Ticket,p['id'],'packing.manage');check_version(t,p.get('expected_version'));previous_native=t.native_receipt_id;edit_ticket(db,actor,t,p)
    d=finance.locked(db,PackingDocument,t.document_id);d.version+=1;db.flush();return ticket_json(db,t)|{'previous_native_receipt_id':previous_native}


@command('ticket.arrive','id expected_version pieces kg cbm occurred_on source mode','packing.arrive',Ticket,financial=True)
def arrive(db,actor,p):
    t=preview_access(db,actor,Ticket,p['id'],'packing.arrive');check_version(t,p.get('expected_version'))
    d=finance.locked(db,PackingDocument,t.document_id)
    if t.state!='active' or d.state!='active':raise DomainError('ticket_void','Cannot arrive void cargo',409)
    optional_kg=nonkg_sea(db,t)
    if any(getattr(t,k) is None for k in ('receipt','pieces','cbm')) or (t.kg is None and not optional_kg):raise DomainError('quantities_unresolved','Confirm original receipt and missing quantities before recording arrival',409)
    mode=enum(p['mode'],('air','sea'),'arrival_mode')
    if (mode=='air' and t.transport not in ('AIR','EXPRESS')) or (mode=='sea' and t.transport!='SEA'):raise DomainError('arrival_mode','Arrival action does not match transport')
    if p.get('kg') is None and not optional_kg:raise DomainError('quantities_unresolved','Known/KG-priced quantities require actual KG',409)
    quantity={'pieces':integer(p['pieces'],True),'kg':finance.decimal(p['kg'],6) if p.get('kg') is not None else None,'cbm':finance.decimal(p['cbm'],6)}
    prior=arrival_projection(db,t);rows=[a for a,_,_ in prior]
    sums={k:known_sum(getattr(a,k) for a in rows) for k in quantity}
    if any(sums[k] is None or quantity[k] is None or sums[k]+quantity[k]>getattr(t,k) for k in quantity if getattr(t,k) is not None):raise DomainError('arrival_remaining','Arrival exceeds remaining quantity',409)
    complete=quantity_complete(t,{k:known_sum((sums[k],quantity[k])) for k in quantity})
    if sums['pieces']+quantity['pieces']==t.pieces and not complete:raise DomainError('arrival_remainder','Final pieces must consume exact remaining KG/CBM')
    from types import SimpleNamespace
    incoming=SimpleNamespace(**quantity)
    _,usd,ngn,charge_basis=allocation.projection(db,t,rows+[incoming])[-1]
    if not str(p.get('source','')).strip():raise DomainError('arrival_source','Actual arrival source required')
    db.add(Arrival(ticket_id=t.id,occurred_on=finance.day(p['occurred_on']),**quantity,freight_usd=usd,clearing_ngn=ngn,charge_basis=charge_basis,actor_id=actor.id,source=p['source']));db.flush()
    bump(t);sync_invoice(db,actor,t,p,automatic_arrival=True);d.version+=1
    shipment=finance.locked(db,Shipment,d.shipment_id)
    shipment.state='partly_arrived'
    active=list(db.scalars(select(Ticket).join(PackingDocument,PackingDocument.id==Ticket.document_id).where(PackingDocument.shipment_id==shipment.id,PackingDocument.state=='active',Ticket.state=='active')))
    if active and all(quantity_complete(item,{k:known_sum(getattr(a,k) for a in db.scalars(select(Arrival).where(Arrival.ticket_id==item.id))) for k in ('pieces','kg','cbm')}) for item in active):shipment.state='arrived'
    shipment.version+=1;db.flush();return ticket_json(db,t)


@command('ticket.attach','id expected_version attachment_id','packing.manage',Ticket)
def attach_ticket(db,actor,p):
    from app.bank.service import attachment_for_actor
    t=preview_access(db,actor,Ticket,p['id'],'packing.manage');check_version(t,p.get('expected_version'))
    a=attachment_for_actor(db,actor,p['attachment_id'])
    db.add(TicketAttachment(ticket_id=t.id,attachment_id=a.id));bump(t);db.flush();return ticket_json(db,t)


@command('ticket.review','id expected_version decision','packing.review',Ticket)
def review_ticket(db,actor,p):
    t=preview_access(db,actor,Ticket,p['id'],'packing.review');check_version(t,p.get('expected_version'))
    decision=enum(p.get('decision','approved'),('approved','rejected'),'decision')
    db.add(TicketReview(ticket_id=t.id,target_version=t.version,decision=decision,actor_id=actor.id));t.review_state=decision;t.version+=1;db.flush();return ticket_json(db,t)


@command('document.split','id expected_version ticket_ids reference','packing.manage',PackingDocument,financial=True)
def split_document(db,actor,p):
    d=preview_access(db,actor,PackingDocument,p['id'],'packing.manage');check_version(d,p.get('expected_version'))
    ids=p['ticket_ids']
    if d.state!='active' or not isinstance(ids,list) or not ids or len(set(ids))!=len(ids):raise DomainError('split_selection','Select distinct tickets in active document')
    tickets=[]
    for identity in sorted(ids):
        t=preview_access(db,actor,Ticket,identity,'packing.manage');financial_access(db,actor,t)
        if t.document_id!=d.id or t.state!='active':raise DomainError('split_membership','Selected ticket not active in this document',409)
        tickets.append(t)
    from app.migration.service import ticket_legacy_bills
    from app.models import LegacyBillMember,LegacyTicket
    for ticket in tickets:
        for bill in ticket_legacy_bills(db,ticket.id):
            group=set(db.scalars(select(LegacyTicket.native_id).join(LegacyBillMember,LegacyBillMember.ticket_id==LegacyTicket.id).where(LegacyBillMember.bill_id==bill.id)))
            if None in group or not group.issubset(set(ids)):
                raise DomainError('legacy_group_split','Move the complete historical billing group together',409)
    child=PackingDocument(shipment_id=d.shipment_id,warehouse_id=d.warehouse_id,reference=p['reference'],uploaded_on=d.uploaded_on,parent_id=d.id)
    db.add(child);db.flush()
    for t in tickets:t.document_id=child.id;t.version+=1
    # Original files remain source observations linked to the unchanged ticket IDs.
    for r in db.scalars(select(DocumentRevision).where(DocumentRevision.document_id==d.id)):
        db.add(DocumentRevision(document_id=child.id,batch_id=r.batch_id,target_version=child.version,mapping={'inherited_ticket_ids':ids},difference={'split_from':d.id},actor_id=actor.id))
    d.version+=1;db.flush();return {'id':d.id,'warehouse_id':d.warehouse_id,'version':d.version,'new_document':document_json(db,child),'original_document':document_json(db,d)}


def void_preview(db,actor,identity):
    # Read preview follows the same guard ordering as mutations.
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    d=preview_access(db,actor,PackingDocument,identity,'packing.manage');rows=[]
    for t in db.scalars(select(Ticket).where(Ticket.document_id==d.id).order_by(Ticket.id)):
        financial_access(db,actor,t)
        from app.migration.service import guard_legacy_ticket
        guard_legacy_ticket(db,t.id,destructive=True)
        impact=finance.adjustment_preview(db,actor,t.invoice_id,'0','0') if t.invoice_id else None
        rows.append({'ticket_id':t.id,'version':t.version,'invoice_id':t.invoice_id,'finance_preview':impact})
    return {'id':d.id,'version':d.version,'tickets':rows}


def ticket_void_preview(db,actor,identity):
    from app.migration.service import guard_legacy_ticket
    guard_legacy_ticket(db,identity,destructive=True)
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    t=preview_access(db,actor,Ticket,identity,'packing.manage');require(actor,'packing.read',t.warehouse_id);financial_access(db,actor,t)
    impact=finance.adjustment_preview(db,actor,t.invoice_id,'0','0') if t.invoice_id else None
    return {'id':t.id,'version':t.version,'document_id':t.document_id,'invoice_id':t.invoice_id,'finance_preview':impact}


@command('ticket.void','id expected_version preview reason','packing.manage',Ticket,financial=True)
def void_ticket(db,actor,p):
    t=preview_access(db,actor,Ticket,p['id'],'packing.manage');check_version(t,p.get('expected_version'))
    preview=ticket_void_preview(db,actor,t.id)
    if p.get('preview')!=preview:raise DomainError('preview_changed','Ticket financial relationships changed',409,preview)
    if t.state!='active' or not str(p.get('reason','')).strip():raise DomainError('void_basis','Active ticket and explicit reason required')
    if t.invoice_id:
        inv=finance.locked(db,Invoice,t.invoice_id)
        finance.adjust_invoice(db,actor,dict(id=inv.id,expected_version=inv.version,amount_ngn='0',discount_total_ngn='0',reason=p['reason'],preview=preview['finance_preview'],idempotency_key=p['idempotency_key']+':invoice'))
        inv.state='void'
    t.state='void';bump(t);d=finance.locked(db,PackingDocument,t.document_id);d.version+=1
    db.flush();return ticket_json(db,t)


@command('document.void','id expected_version preview reason','packing.manage',PackingDocument,financial=True)
def void_document(db,actor,p):
    d=preview_access(db,actor,PackingDocument,p['id'],'packing.manage');check_version(d,p.get('expected_version'))
    current=void_preview(db,actor,d.id)
    if p.get('preview')!=current:raise DomainError('preview_changed','Document financial relationships changed',409,current)
    if not str(p.get('reason','')).strip():raise DomainError('reason_required','Explicit cancellation reason required')
    for row in current['tickets']:
        t=finance.locked(db,Ticket,row['ticket_id'])
        if t.invoice_id:
            inv=finance.locked(db,Invoice,t.invoice_id)
            finance.adjust_invoice(db,actor,dict(id=inv.id,expected_version=inv.version,amount_ngn='0',discount_total_ngn='0',reason=p['reason'],preview=row['finance_preview'],idempotency_key=p['idempotency_key']+':'+t.id))
            inv.state='void'
        t.state='void';bump(t)
    d.state='void';d.version+=1;db.flush();return document_json(db,d)


def recover_preview(db,actor,identity):
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    d=preview_access(db,actor,PackingDocument,identity,'packing.manage');rows=[]
    for t in db.scalars(select(Ticket).where(Ticket.document_id==d.id).order_by(Ticket.id)):
        from app.migration.service import guard_legacy_ticket
        guard_legacy_ticket(db,t.id,destructive=True)
        preview=ticket_change_preview(db,actor,t.id,{}) if t.invoice_id else None
        rows.append({'ticket_id':t.id,'version':t.version,'invoice_id':t.invoice_id,'finance_preview':preview['finance_preview'] if preview else None})
    return {'id':d.id,'version':d.version,'tickets':rows}


@command('document.recover','id expected_version reason preview','packing.manage',PackingDocument,financial=True)
def recover_document(db,actor,p):
    d=preview_access(db,actor,PackingDocument,p['id'],'packing.manage');check_version(d,p.get('expected_version'))
    if d.state!='void' or not str(p.get('reason','')).strip():raise DomainError('recover_basis','Void document and recovery reason required')
    preview=recover_preview(db,actor,d.id)
    if p.get('preview')!=preview:raise DomainError('preview_changed','Review financial restoration before recovery',409,preview)
    # Recovery restores cargo only; prior credits remain in cash balance. Existing AR
    # is explicitly restored with a new adjustment and never re-allocates old cash.
    for t in db.scalars(select(Ticket).where(Ticket.document_id==d.id).order_by(Ticket.id)):
        financial_access(db,actor,t);t.state='active';bump(t)
        if t.invoice_id:
            inv=finance.locked(db,Invoice,t.invoice_id);inv.state='active'
            impact=next(row['finance_preview'] for row in preview['tickets'] if row['ticket_id']==t.id)
            sync_invoice(db,actor,t,dict(p,idempotency_key=p['idempotency_key']+':'+t.id,finance_preview=impact))
    d.state='active';d.version+=1;db.flush();return document_json(db,d)


def list_documents(db,actor,limit=100,offset=0,shipment_id=None,state=None):
    require(actor,'packing.read')
    if not 1<=limit<=200 or offset<0:raise DomainError('pagination','Invalid pagination')
    q=visible(select(PackingDocument),PackingDocument,actor)
    if shipment_id:q=q.where(PackingDocument.shipment_id==shipment_id)
    if state:q=q.where(PackingDocument.state==state)
    return [finance.record(d) for d in db.scalars(q.order_by(PackingDocument.created_at.desc(),PackingDocument.id).limit(limit).offset(offset))]


def list_tickets(db,actor,limit=50,offset=0,receipt=None,client=None,item=None,destination=None,transport=None,state=None,document_id=None):
    from sqlalchemy import or_
    from app.models import Party
    require(actor,'packing.read')
    if not 1<=limit<=200 or offset<0:raise DomainError('pagination','Use limit 1..200 and nonnegative offset')
    q=visible(select(Ticket),Ticket,actor)
    for field,value in [(Ticket.receipt,receipt),(Ticket.destination,destination)]:
        if value:q=q.where(field.icontains(value,autoescape=True))
    if item:q=q.where(or_(Ticket.item_zh.icontains(item,autoescape=True),Ticket.item_en.icontains(item,autoescape=True)))
    if client:q=q.outerjoin(Party,Party.id==Ticket.party_id).where(or_(Party.name.icontains(client,autoescape=True),Ticket.mark.icontains(client,autoescape=True)))
    for field,value in [(Ticket.transport,transport),(Ticket.state,state),(Ticket.document_id,document_id)]:
        if value:q=q.where(field==value)
    result=[]
    for ticket in db.scalars(q.order_by(Ticket.created_at.desc(),Ticket.id).limit(limit).offset(offset)):
        if ticket.invoice_id:finance.get_invoice(db,actor,ticket.invoice_id)
        result.append(ticket_json(db,ticket))
    return result


def document_groups(db,actor,identity):
    d=get_document(db,actor,identity);tickets=[t for t in d['tickets'] if t['state']=='active'];parent={t['id']:t['id'] for t in tickets};seen={}
    def root(identity):
        while parent[identity]!=identity:
            parent[identity]=parent[parent[identity]];identity=parent[identity]
        return identity
    for t in tickets:
        keys=[]
        if t.get('invoice_id'):keys.append(('invoice',t['invoice_id']))
        if t['mark']:keys.append(('mark',t['mark']))
        if t['phone_confirmed'] and t['phone_normalized']:keys.append(('phone',t['phone_normalized']))
        for key in keys:
            if key in seen:parent[root(t['id'])]=root(seen[key])
            else:seen[key]=t['id']
    groups={}
    for t in tickets:groups.setdefault(root(t['id']),[]).append(t)
    result=[]
    for group in groups.values():
        phones=sorted({t['contact_raw'] for t in group if t['contact_raw']});destinations=sorted({t['destination'] for t in group if t['destination']})
        result.append({'ticket_ids':[t['id'] for t in group],'member_versions':{t['id']:t['version'] for t in group},'marks':sorted({t['mark'] for t in group if t['mark']}),'phones':phones,'destinations':destinations,'phone_conflict':len(phones)>1,'destination_conflict':len(destinations)>1,'document_version':d['version']})
    return result
