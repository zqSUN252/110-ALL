"""Explicit native receipt reservations and actual outbound-to-arrival conservation."""
from decimal import Decimal
from sqlalchemy import select, func
from app.common import DomainError, require, check_version
from app.models import Receipt, Ticket, Arrival, PackingDocument, StockEvent, DispatchBatch, ArrivalDispatch
from app.finance.service import locked, record, decimal
from app.cargo.commands import fields, integer,command,preview_access,bump

KEYS=('pieces','kg','cbm')


def quantity_text(value):
    """Display a conserved quantity without storage-scale trailing zeros."""
    return format(Decimal(value).normalize(),'f')


def reservation(db,ticket):
    if not ticket.native_receipt_id:return
    source=locked(db,Receipt,ticket.native_receipt_id)
    if source.state!='active':raise DomainError('native_source','Native source must remain active',409)
    rows=list(db.scalars(select(Ticket).where(Ticket.native_receipt_id==source.id,Ticket.id!=ticket.id)))+[ticket]
    used={k:Decimal(0) for k in KEYS}
    for row in rows:
        if row.state=='active':
            for key in KEYS:
                value=getattr(row,key)
                if value is None:
                    if key!='kg':raise DomainError('native_source_unknown','Explicit native source reservation requires PCS and CBM',409)
                    # Unknown actual SEA weight remains unknown. Preserve all
                    # explicitly allocated known outbound KG as a lower bound.
                    value=db.scalar(select(func.sum(ArrivalDispatch.kg)).join(Arrival,Arrival.id==ArrivalDispatch.arrival_id).where(Arrival.ticket_id==row.id)) or 0
                used[key]+=Decimal(value)
        else:
            # A business void cannot return actual transported cargo to the origin.
            for key in KEYS:
                value=db.scalar(select(func.sum(getattr(ArrivalDispatch,key))).join(Arrival,Arrival.id==ArrivalDispatch.arrival_id).where(Arrival.ticket_id==row.id))
                observed=db.scalar(select(func.sum(getattr(Arrival,key))).where(Arrival.ticket_id==row.id))
                value=max(value or 0,observed or 0)
                used[key]+=Decimal(value or 0)
    if any(used[k]>Decimal(getattr(source,k)) for k in KEYS):
        raise DomainError('native_source_quantity','Ticket allocations exceed the shared native source quantity',409)


def dispatch_options(db,actor,ticket):
    if not ticket.native_receipt_id:return []
    source=locked(db,Receipt,ticket.native_receipt_id);require(actor,'cargo.read',source.warehouse_id)
    document=locked(db,PackingDocument,ticket.document_id)
    events=list(db.scalars(select(StockEvent).join(DispatchBatch,DispatchBatch.id==StockEvent.batch_id)
        .where(StockEvent.receipt_id==source.id,StockEvent.kind=='outbound',DispatchBatch.shipment_id==document.shipment_id).order_by(StockEvent.created_at,StockEvent.id)))
    consumed={row[0]:dict(zip(KEYS,row[1:])) for row in db.execute(select(ArrivalDispatch.stock_event_id,func.sum(ArrivalDispatch.pieces),func.sum(ArrivalDispatch.kg),func.sum(ArrivalDispatch.cbm)).where(ArrivalDispatch.stock_event_id.in_([e.id for e in events])).group_by(ArrivalDispatch.stock_event_id))}
    return [record(e)|{'remaining':{k:quantity_text(-getattr(e,k)-(consumed.get(e.id,{}).get(k) or 0)) for k in KEYS}} for e in events]


def arrive_sources(db,actor,ticket,arrival,selection):
    if not ticket.native_receipt_id:
        if selection:raise DomainError('native_source','External unlinked ticket cannot claim native stock',409)
        return
    reservation(db,ticket)
    if not isinstance(selection,list) or not selection or len(selection)>200:
        raise DomainError('native_dispatch_required','Select actual native outbound batches and quantities for this arrival',409)
    options={row['id']:row for row in dispatch_options(db,actor,ticket)}
    sums={k:Decimal(0) for k in KEYS};seen=set()
    for requested in selection:
        fields(requested,'stock_event_id expected_version pieces kg cbm')
        identity=requested['stock_event_id'];event=options.get(identity)
        if identity in seen or not event:raise DomainError('native_dispatch_scope','Choose distinct actual outbound batches of this source and shipment',409)
        seen.add(identity)
        if requested.get('expected_version')!=event['version']:raise DomainError('stale_version','Outbound source changed',409)
        quantities={'pieces':integer(requested['pieces'],True),'kg':decimal(requested['kg'],6),'cbm':decimal(requested['cbm'],6)}
        if any(Decimal(quantities[k])>Decimal(event['remaining'][k]) for k in KEYS):raise DomainError('native_dispatch_remaining','Arrival exceeds actual outbound source remaining',409)
        for key in KEYS:sums[key]+=Decimal(quantities[key])
        db.add(ArrivalDispatch(arrival_id=arrival.id,stock_event_id=identity,source_version=event['version'],receipt_id=ticket.native_receipt_id,**quantities))
    if any(getattr(arrival,k) is not None and sums[k]!=Decimal(getattr(arrival,k)) for k in KEYS):raise DomainError('native_dispatch_total','Outbound allocations must equal this actual arrival in every known quantity',409)


def validate_consumption(db,ticket):
    if not ticket.native_receipt_id:return
    reservation(db,ticket)
    actual=list(db.scalars(select(Arrival).where(Arrival.ticket_id==ticket.id)))
    for arrival in actual:
        links=list(db.scalars(select(ArrivalDispatch).where(ArrivalDispatch.arrival_id==arrival.id)))
        if not links or any(getattr(arrival,k) is not None and sum((Decimal(getattr(link,k)) for link in links),Decimal(0))!=getattr(arrival,k) for k in KEYS):
            raise DomainError('native_dispatch_unresolved','Actual arrival requires its preserved native outbound allocation',409)
        for link in links:
            event=locked(db,StockEvent,link.stock_event_id)
            document=locked(db,PackingDocument,ticket.document_id)
            if event.receipt_id!=ticket.native_receipt_id or event.version!=link.source_version or event.kind!='outbound' or locked(db,DispatchBatch,event.batch_id).shipment_id!=document.shipment_id:
                raise DomainError('native_dispatch_changed','Actual outbound source relationship changed',409)


@command('ticket.arrival_sources','id expected_version arrival_id arrival_version dispatch_allocations reason','packing.arrive',Ticket,financial=True)
def reconcile(db,actor,p):
    ticket=preview_access(db,actor,Ticket,p['id'],'packing.arrive');check_version(ticket,p.get('expected_version'))
    arrival=locked(db,Arrival,p['arrival_id']);check_version(arrival,p.get('arrival_version'))
    if arrival.ticket_id!=ticket.id or not ticket.native_receipt_id or not str(p.get('reason') or '').strip():raise DomainError('native_dispatch_scope','Select this ticket actual arrival and source evidence',409)
    if db.scalar(select(ArrivalDispatch.id).where(ArrivalDispatch.arrival_id==arrival.id)):raise DomainError('native_dispatch_preserved','Recorded outbound allocation evidence is immutable',409)
    arrive_sources(db,actor,ticket,arrival,p.get('dispatch_allocations'));bump(ticket);db.flush()
    from app.packing.service import ticket_json
    return ticket_json(db,ticket)
