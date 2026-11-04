"""One lock order and replay ACL for logistics commands, including finance orchestration."""
from functools import wraps
from sqlalchemy import select,text
from app.common import DomainError,require,audit,run_command,json_value,check_version
from app.finance.service import locked,record,decimal,day
from app.finance.access import party_member
from app.models import Warehouse,Party,Invoice,Receipt,Shipment,Ticket,PackingDocument,ImportBatch


def port(value):
    value=str(value or '').strip().upper()
    return 'PORT HARCOURT' if value in ('P.H','PH') else value or None


def enum(value,allowed,name):
    if value not in allowed:raise DomainError('invalid_'+name,'Unsupported '+name)
    return value


def fields(p,allowed):
    extra=set(p)-set(allowed.split())
    if extra:raise DomainError('unknown_fields','Unsupported fields',422,{'fields':sorted(extra)})


def integer(value,positive=False):
    if not isinstance(value,int) or isinstance(value,bool) or value<(1 if positive else 0) or value>100000000:
        raise DomainError('invalid_pieces','Pieces must be a bounded whole number')
    return value


def member(db,actor,party_id,warehouse_id,capability):
    require(actor,capability,warehouse_id)
    if party_id:
        party=locked(db,Party,party_id)
        party_member(db,party.id,warehouse_id)
    locked(db,Warehouse,warehouse_id)


def object_access(db,actor,cls,identity,capability,expected=None):
    obj=locked(db,cls,identity)
    require(actor,capability,getattr(obj,'warehouse_id',obj.id if cls is Warehouse else None))
    if cls is Ticket and obj.native_receipt_id:
        object_access(db,actor,Receipt,obj.native_receipt_id,'cargo.read')
    if expected is not None:check_version(obj,expected)
    return obj


def preview_access(db,actor,cls,identity,capability):
    obj=object_access(db,actor,cls,identity,capability)
    require(actor,capability.split('.')[0]+'.read',getattr(obj,'warehouse_id',None))
    return obj


def native_link(db,actor,warehouse_id,identity,receipt=None,party_id=None,transport=None):
    if not identity:return
    native=object_access(db,actor,Receipt,identity,'cargo.read')
    if native.warehouse_id!=warehouse_id:raise DomainError('receipt_scope','Native receipt belongs to another warehouse',403)
    if native.state!='active' or (receipt is not None and native.reference!=receipt) or (party_id and native.party_id and party_id!=native.party_id) or (transport and native.transport not in ('WAREHOUSE',transport)):
        raise DomainError('receipt_identity','Native receipt must be active and match known receipt, party and transport',409)
    return native


def scope_record(db,actor,value,capability):
    """Recheck current graph plus the original returned warehouse facts on every replay."""
    if isinstance(value,list):
        for child in value:scope_record(db,actor,child,capability)
    elif isinstance(value,dict):
        for key in ('native_receipt_id','previous_native_receipt_id'):
            if value.get(key):object_access(db,actor,Receipt,value[key],'cargo.read')
        if value.get('warehouse_id'):require(actor,capability,value['warehouse_id'])
        for key,cls in [('receipt_id',Receipt),('shipment_id',Shipment),('document_id',PackingDocument),('ticket_id',Ticket),('batch_id',ImportBatch)]:
            if value.get(key):
                obj=db.get(cls,value[key])
                if obj:require(actor,capability,obj.warehouse_id)
        for child in value.values():
            if isinstance(child,(dict,list)):scope_record(db,actor,child,capability)


def command(name,allowed,capability,model=None,financial=False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(db,actor,p):
            fields(p,allowed+' idempotency_key')
            require(actor,capability)
            read_capability=capability.split('.')[0]+'.read'
            require(actor,read_capability)
            # All orchestration takes this guard BEFORE any command/id/row lock.
            db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
            scope_record(db,actor,p,capability)
            scope_record(db,actor,p,read_capability)
            if model and p.get('id'):
                object_access(db,actor,model,p['id'],capability)
                object_access(db,actor,model,p['id'],read_capability)
            def apply():
                try:result=fn(db,actor,p)
                except KeyError:raise DomainError('missing_field','Required command field missing',422) from None
                db.flush();result=json_value(result)
                audit(db,actor,name,name.split('.')[0],result.get('id','batch'),None,result)
                return result
            result=run_command(db,actor,name,p.get('idempotency_key'),p,apply)
            scope_record(db,actor,result,capability)
            scope_record(db,actor,result,read_capability)
            if financial:
                def invoices(value):
                    if isinstance(value,list):
                        for v in value:yield from invoices(v)
                    if isinstance(value,dict):
                        for k,v in value.items():
                            if k in ('invoice_id','clearing_invoice_id') and v:yield v
                            elif isinstance(v,(dict,list)):yield from invoices(v)
                for identity in set(invoices(result)):
                    inv=locked(db,Invoice,identity)
                    require(actor,'payables.manage' if inv.kind=='AP' else 'finance.invoice',inv.warehouse_id)
                    require(actor,'payables.read' if inv.kind=='AP' else 'finance.read',inv.warehouse_id)
                    from app.finance.authorization import authorize
                    def discount_requested(value):
                        if isinstance(value,list):return any(discount_requested(v) for v in value)
                        if isinstance(value,dict):return any((k=='discount_total_ngn' and v is not None) or discount_requested(v) for k,v in value.items())
                        return False
                    authorize(db,actor,'invoice.adjust',{'id':inv.id,**({'discount_total_ngn':'0'} if name in ('document.void','ticket.void') or discount_requested(p) else {})})
            return result
        return wrapped
    return decorate


def bump(obj):
    obj.version+=1
    if hasattr(obj,'review_state'):obj.review_state='pending'


def visible(query,cls,actor):
    return query if '*' in actor.capabilities else query.where(cls.warehouse_id.in_(actor.warehouse_ids))
