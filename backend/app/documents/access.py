"""Document commands preserve the shared guard order and current source authorization."""
from functools import wraps
from datetime import datetime
import hashlib
import json
from sqlalchemy import select,text
from app.common import DomainError,require,run_command,audit,json_value
from app.models import Ticket,Receipt,PackingDocument,Shipment,NotificationMember,ReleaseMember,ExportMember,DocumentExport,MBLRecord,Notification,CopyEvent,HoldFact,Invoice,Payment,EvidenceCase
from app.finance.service import locked
from app.cargo.commands import fields


def instant(value):
    try:
        v=datetime.fromisoformat(value) if isinstance(value,str) else value
        if not isinstance(v,datetime) or v.tzinfo is None:raise ValueError()
        return v
    except (ValueError,TypeError):raise DomainError('invalid_time','Timezone-aware ISO event time required') from None


def fingerprint(value):
    return hashlib.sha256(json.dumps(json_value(value),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def sources(db, p):
    """Resolve stable sources, including saved membership after regrouping."""
    result=[]
    for tid in p.get('ticket_ids',[]):result.append(locked(db,Ticket,tid))
    for rid in p.get('receipt_ids',[]):result.append(locked(db,Receipt,rid))
    for member in p.get('members',[]):
        if member.get('ticket_id'):result.append(locked(db,Ticket,member['ticket_id']))
        if member.get('hold_id'):
            h=locked(db,HoldFact,member['hold_id']);result.append(locked(db,Ticket if h.ticket_id else Receipt,h.ticket_id or h.receipt_id))
    for key,cls in [('ticket_id',Ticket),('receipt_id',Receipt),('shipment_id',Shipment),('document_id',PackingDocument),('invoice_id',Invoice),('payment_id',Payment),('case_id',EvidenceCase)]:
        if p.get(key):result.append(locked(db,cls,p[key]))
    for key,model,fk in [('order_id',ReleaseMember,'order_id'),('notification_id',NotificationMember,'notification_id'),('export_id',ExportMember,'export_id')]:
        if p.get(key):
            for m in db.scalars(select(model).where(getattr(model,fk)==p[key])):
                result.append(locked(db,Ticket if m.ticket_id else Receipt,m.ticket_id or m.receipt_id))
    if p.get('mbl_id'):
        m=locked(db,MBLRecord,p['mbl_id']);result.append(locked(db,Shipment,m.shipment_id))
    if p.get('target_type') in ('ticket','shipment'):
        result.append(locked(db,Ticket if p['target_type']=='ticket' else Shipment,p['target_id']))
    return result


def authorize(db,actor,p,capability):
    require(actor,capability)
    require(actor,capability.split('.')[0]+'.read')
    for obj in sources(db,p):
        if capability=='release.physical' and isinstance(obj,Ticket) and p.get('ticket_id') and obj.id!=p['ticket_id']:continue
        wh=obj.destination_warehouse_id if capability in ('release.physical',) and isinstance(obj,Ticket) else obj.warehouse_id
        if capability=='tracking.manage' and p.get('node')=='warehouse_arrived':
            wh=p.get('destination_warehouse_id')
            if not wh:raise DomainError('warehouse_required','Actual destination warehouse is required')
        require(actor,capability,wh)
        require(actor,capability.split('.')[0]+'.read',wh)
        if capability.startswith('documents.') and isinstance(obj,Ticket) and obj.invoice_id:
            from app.finance.service import get_invoice
            get_invoice(db,actor,obj.invoice_id)
        if capability.startswith('documents.') and isinstance(obj,Ticket) and obj.native_receipt_id:
            require(actor,capability,locked(db,Receipt,obj.native_receipt_id).warehouse_id)
    if capability.startswith('documents.'):
        for key,cls in [('notification_id',Notification),('export_id',DocumentExport)]:
            if p.get(key):
                stored=locked(db,cls,p[key])
                for row in (stored.source_snapshot or {}).get('rows',[]):
                    require(actor,capability,row['warehouse_id'])
                    if row.get('invoice_id'):
                        from app.finance.service import get_invoice
                        get_invoice(db,actor,row['invoice_id'])
                for debt in (stored.source_snapshot or {}).get('credit_debt',{}).get('invoices',[]):
                    from app.finance.service import get_invoice
                    get_invoice(db,actor,debt['invoice_id'])
                if isinstance(stored,Notification) and stored.order_id:
                    from app.finance.access import payment_access
                    for member in db.scalars(select(ReleaseMember).where(ReleaseMember.order_id==stored.order_id)):
                        for pay in member.source_snapshot.get('payments',[]):payment_access(db,actor,locked(db,Payment,pay['id']),'finance.read')


def command(name, allowed, capability, identity=None):
    def decorate(fn):
        @wraps(fn)
        def wrapped(db,actor,p):
            fields(p,allowed+' idempotency_key')
            db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
            q=dict(p)
            if identity and p.get('id'):q[identity]=p['id']
            if identity=='copy_id':
                q['notification_id']=locked(db,CopyEvent,p['id']).notification_id
            if identity=='hold_id':
                h=locked(db,HoldFact,p['id']);q['ticket_id' if h.ticket_id else 'receipt_id']=h.ticket_id or h.receipt_id
            authorize(db,actor,q,capability)
            if name=='release.issue':
                for obj in sources(db,q):
                    require(actor,'finance.read',obj.warehouse_id)
                    if p.get('mode')=='credit':require(actor,'release.credit',obj.warehouse_id)
                    if p.get('mode')=='waiver':require(actor,'release.waiver',obj.warehouse_id);require(actor,'finance.discount',obj.warehouse_id)
            if name=='payment.register':
                from app.finance.access import payment_access
                inv=locked(db,Invoice,p['invoice_id']);require(actor,'finance.read',inv.warehouse_id)
                if p.get('payment_id'):payment_access(db,actor,locked(db,Payment,p['payment_id']),'finance.read')
                if p.get('case_id'):require(actor,'evidence.read',locked(db,EvidenceCase,p['case_id']).warehouse_id)
            if name=='hold.remove':
                from app.packing.service import financial_access
                for obj in sources(db,q):
                    if isinstance(obj,Ticket):
                        require(actor,'packing.manage',obj.warehouse_id);require(actor,'packing.read',obj.warehouse_id);financial_access(db,actor,obj)
                    elif isinstance(obj,Receipt):require(actor,'cargo.manage',obj.warehouse_id);require(actor,'cargo.read',obj.warehouse_id)
            def apply():
                try:r=fn(db,actor,p)
                except KeyError:raise DomainError('missing_field','Required command field missing',422) from None
                db.flush();r=json_value(r);audit(db,actor,name,'documents',r.get('id','batch'),None,r);return r
            r=run_command(db,actor,name,p.get('idempotency_key'),p,apply)
            authorize(db,actor,r,capability)
            # Original immutable membership authorization is repeated on replay.
            if identity:authorize(db,actor,q,capability)
            return r
        return wrapped
    return decorate


def own_upload(db,actor,identity):
    from app.models import Attachment
    a=locked(db,Attachment,identity)
    if '*' not in actor.capabilities and not (a.owner_type=='user' and a.owner_id==actor.id):
        raise DomainError('forbidden','Original upload is outside your scope',403)
    return a
