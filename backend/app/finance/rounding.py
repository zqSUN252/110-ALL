"""Reviewed allocation of an existing cargo charge; never an arbitrary rounding input."""
from decimal import Decimal
from app.common import DomainError,json_value
from app.models import Ticket,ArrivalSettlement


def cargo_basis(db,ticket):
    from app.packing.quantities import known_sum,complete as quantity_complete
    from app.packing.service import arrival_projection,charges,invoice_amount
    from app.finance.service import settle
    from app.packing import allocation
    values=charges(db,ticket);projection=arrival_projection(db,ticket)
    if values is None or not projection or any(usd is None or ngn is None for _,usd,ngn in projection):raise DomainError('rounding_basis','Actual priced arrivals required for settlement allocation')
    full_usd,full_ngn=values;control,_=allocation.settlement_control_and_quota(db,ticket,{'pieces':0,'kg':Decimal(0),'cbm':Decimal(0)})
    result=[];settled=Decimal('0.00');quantities={'pieces':0,'kg':Decimal(0),'cbm':Decimal(0)}
    for arrival,usd,ngn in projection:
        for key in quantities:quantities[key]=known_sum((quantities[key],getattr(arrival,key)))
        complete=quantity_complete(ticket,quantities)
        native=invoice_amount(usd,ngn,ticket.ngn_per_usd)
        if settled>control:raise DomainError('rounding_allocation','Prior allocation exceeds original charge control',409)
        _,quota=allocation.settlement_control_and_quota(db,ticket,quantities)
        amount=control-settled if complete else min(native,control-settled,max(Decimal(0),quota-settled))
        settled+=amount
        result.append({'arrival_id':arrival.id,'arrival_version':arrival.version,'freight_usd':usd,'charge_ngn':ngn,'settled_ngn':amount})
    usd=sum((row['freight_usd'] for row in result),Decimal('0.00'));ngn=sum((row['charge_ngn'] for row in result),Decimal('0.00'))
    difference=settled-invoice_amount(usd,ngn,ticket.ngn_per_usd)
    return json_value({'ticket_id':ticket.id,'ticket_version':ticket.version,'warehouse_id':ticket.warehouse_id,'party_id':ticket.party_id,'original_total_ngn':control,'charge_usd':usd,'charge_ngn':ngn,'ngn_per_usd':ticket.ngn_per_usd,'amount_ngn':settled,'rounding_adjustment_ngn':difference,'source_quantities':{key:getattr(ticket,key) for key in ('pieces','kg','cbm')},'components':allocation.components(db,ticket),'arrivals':result})


def validate_basis(db,basis,amount,invoice=None,payload=None):
    if not isinstance(basis,dict) or not basis.get('ticket_id'):raise DomainError('rounding_basis','Verified cargo allocation basis required')
    from app.finance.service import locked,decimal
    ticket=locked(db,Ticket,basis['ticket_id']);current=cargo_basis(db,ticket)
    if basis!=current or decimal(amount)!=Decimal(current['amount_ngn']):raise DomainError('rounding_basis_changed','Cargo charge allocation basis changed',409)
    if invoice:
        if ticket.invoice_id!=invoice.id or invoice.party_id!=ticket.party_id or invoice.warehouse_id!=ticket.warehouse_id:raise DomainError('rounding_relationship','Invoice must be the ticket existing related receivable',403)
    elif payload:
        if payload.get('source_key')!='ticket:'+ticket.id or payload.get('party_id')!=ticket.party_id or payload.get('warehouse_id')!=ticket.warehouse_id or payload.get('kind','AR')!='AR':raise DomainError('rounding_relationship','New receivable must use exact priced ticket identity',403)
    if payload is not None:
        for key in ('charge_usd','charge_ngn'):
            if decimal(payload.get(key,'0'))!=Decimal(current[key]):raise DomainError('rounding_components','Typed components must equal actual arrived charge allocation')
        if Decimal(current['charge_usd']) and decimal(payload.get('ngn_per_usd'),6,True)!=Decimal(current['ngn_per_usd']):raise DomainError('rounding_components','Explicit FX must match original cargo quote')
    return Decimal(current['rounding_adjustment_ngn']),current


def save_basis(db,actor,invoice,basis):
    invoice.pricing=dict(invoice.pricing or {})|{'arrival_allocation':basis}
    for row in basis['arrivals']:
        db.add(ArrivalSettlement(invoice_id=invoice.id,invoice_version=invoice.version,ticket_id=basis['ticket_id'],ticket_version=basis['ticket_version'],arrival_id=row['arrival_id'],arrival_version=row['arrival_version'],freight_usd=Decimal(row['freight_usd']),charge_ngn=Decimal(row['charge_ngn']),ngn_per_usd=Decimal(basis['ngn_per_usd']) if basis['ngn_per_usd'] else None,settled_ngn=Decimal(row['settled_ngn']),original_total_ngn=Decimal(basis['original_total_ngn']),actor_id=actor.id))
