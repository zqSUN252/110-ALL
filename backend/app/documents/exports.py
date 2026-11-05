"""Persistent exports: short source transactions, rendering outside the shared guard."""
from datetime import timedelta
from sqlalchemy import select,text
from app.common import Actor,DomainError,require,run_command,audit
from app.models import Ticket,Receipt,Job,DocumentExport,ExportMember,User,Grant,WarehouseScope,CustomerIdentity,Attachment,utcnow
from app.finance.service import locked,record
from app.attachments import put_file,read_file
from .access import command,authorize,fingerprint
from .projection import packing_projection
from .rendering import render_artifact


def members(db,e):
    # The immutable projection is the selection order, including pre-fix jobs.
    # Random member UUIDs never define business row order.
    saved=list(db.scalars(select(ExportMember).where(ExportMember.export_id==e.id)))
    indexed={('ticket' if m.ticket_id else 'receipt',m.ticket_id or m.receipt_id):m for m in saved}
    keys=[(r['source_type'],r['id']) for r in e.source_snapshot.get('rows',[])]
    if len(saved)!=len(indexed) or len(keys)!=len(set(keys)) or set(keys)!=set(indexed):
        raise DomainError('stale_export','Saved export membership changed; request a new export',409)
    return [locked(db,Ticket if kind=='ticket' else Receipt,identity) for kind,identity in keys]


def refresh_actor(db,uid):
    u=db.get(User,uid)
    if not u or not u.active:raise DomainError('forbidden','Exporter is inactive',403)
    return Actor(uid,set(db.scalars(select(Grant.capability).where(Grant.user_id==uid))),set(db.scalars(select(WarehouseScope.warehouse_id).where(WarehouseScope.user_id==uid))))


def authorize_export(db,e,actor=None,customer=None):
    if e.mbl_id:
        if customer:raise DomainError('not_found','Customer export unavailable',404)
        from .mbl import access
        access(db,actor,e.mbl_id,'documents.export');require(actor,'documents.read')
        return []
    objects=members(db,e)
    if e.customer_id:
        from app.tracking.customer import require_owned
        if not customer or customer.id!=e.customer_id:raise DomainError('not_found','Customer export unavailable',404)
        require_owned(db,customer,objects)
    else:
        if customer:raise DomainError('not_found','Customer export unavailable',404)
        authorize(db,actor,{'export_id':e.id},'documents.export')
        for obj in objects:
            if isinstance(obj,Ticket) and obj.invoice_id:
                from app.finance.service import get_invoice
                get_invoice(db,actor,obj.invoice_id)
    return objects


def current_data(db,e,objects):
    if e.mbl_id:
        from .mbl import snapshot
        return snapshot(db,e.mbl_id,e.kind)
    current=packing_projection(db,objects);current['generated_on']=e.source_snapshot['generated_on'];return current


def _queue(db,actor,p,objects,customer=None):
    if p.get('format') not in ('xlsx','pdf','png') or p.get('kind') not in ('packing','label_fee','label_plain','label_confirmation'):raise DomainError('export_format','Choose a formal packing list or label and an explicit format')
    if not objects:raise DomainError('export_empty','No authorized source rows')
    if p['kind'].startswith('label_') and (len(objects)!=1 or p['format']=='xlsx'):raise DomainError('label_selection','Select one source and PNG/PDF')
    data=packing_projection(db,objects)
    j=Job(kind='document_export',payload={'actor_id':actor.id},idempotency_key='document_export:'+actor.id+':'+p['idempotency_key']);db.add(j);db.flush()
    e=DocumentExport(job_id=j.id,actor_id=actor.id,customer_id=customer.id if customer else None,format=p['format'],kind=p['kind'],source_snapshot=data,source_fingerprint=fingerprint(data));db.add(e);db.flush()
    for obj in objects:db.add(ExportMember(export_id=e.id,ticket_id=obj.id if isinstance(obj,Ticket) else None,receipt_id=obj.id if isinstance(obj,Receipt) else None))
    db.flush();return {'id':e.id,'export_id':e.id,'job_id':j.id,'status':j.status,'version':e.version,'format':e.format,'kind':e.kind}


def selection(db,p):
    objects=[locked(db,Ticket,i) for i in p.get('ticket_ids',[])]+[locked(db,Receipt,i) for i in p.get('receipt_ids',[])]
    if p.get('document_id'):
        if objects:raise DomainError('selection','Choose explicit rows or a full document')
        objects=list(db.scalars(select(Ticket).where(Ticket.document_id==p['document_id'],Ticket.state=='active').order_by(Ticket.position,Ticket.id)))
    if len({(type(o),o.id) for o in objects})!=len(objects):raise DomainError('selection','Duplicate source selection')
    return objects


@command('document.export','ticket_ids receipt_ids document_id format kind','documents.export')
def queue_export(db,actor,p):
    objects=selection(db,p)
    for obj in objects:
        require(actor,'documents.export',obj.warehouse_id);require(actor,'documents.read',obj.warehouse_id)
        if isinstance(obj,Ticket) and obj.invoice_id:
            from app.finance.service import get_invoice
            get_invoice(db,actor,obj.invoice_id)
    return _queue(db,actor,p,objects)


def queue_customer(db,customer,p):
    from app.tracking.customer import actor,require_owned
    from app.cargo.commands import fields
    fields(p,'ticket_ids receipt_ids format kind idempotency_key')
    if p.get('kind')!='packing':raise DomainError('customer_export','Customer exports use the formal packing list')
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'));objects=selection(db,p);require_owned(db,customer,objects);a=actor(customer)
    def apply():
        result=_queue(db,a,p,objects,customer);audit(db,a,'customer.export','document_export',result['id'],None,result);return result
    result=run_command(db,a,'customer.export',p.get('idempotency_key'),p,apply)
    authorize_export(db,locked(db,DocumentExport,result['id']),customer=customer)
    return result


def status(db,identity,actor=None,customer=None):
    e=locked(db,DocumentExport,identity);authorize_export(db,e,actor,customer);j=db.get(Job,e.job_id)
    return {'id':e.id,'version':e.version,'job_id':j.id,'status':j.status,'error_code':j.error_code,'format':e.format,'kind':e.kind,'ready':j.status=='completed' and bool(e.attachment_id)}


def download_attachment(db,identity,actor=None,customer=None):
    e=locked(db,DocumentExport,identity);objects=authorize_export(db,e,actor,customer)
    # Preserve generation date for source comparison; midnight alone does not revoke a formal file.
    current=current_data(db,e,objects)
    if fingerprint(current)!=e.source_fingerprint:raise DomainError('stale_export','Source changed; request a new export',409)
    if not e.attachment_id or db.get(Job,e.job_id).status!='completed':raise DomainError('export_pending','Export is not ready',409)
    a=locked(db,Attachment,e.attachment_id)
    if a.owner_type!='document_export' or a.owner_id!=e.id:raise DomainError('file_scope','Export artifact owner changed',403)
    return a


def download(db,settings,identity,actor=None,customer=None):
    a=download_attachment(db,identity,actor,customer);return read_file(settings,a),a.media_type


def export_once(settings,factory):
    from app.migration.operations import worker_gate,worker_write_gate
    worker_gate(factory,settings)
    e_id=None;lease=None
    try:
        with factory() as db,db.begin():
            worker_write_gate(db,settings)
            j=db.scalar(select(Job).where(Job.kind=='document_export',((Job.status=='pending')|((Job.status=='running')&(Job.locked_at<utcnow()-timedelta(minutes=5))))).order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
            if not j:return False
            e=db.scalar(select(DocumentExport).where(DocumentExport.job_id==j.id));e_id=e.id
            customer=db.get(CustomerIdentity,e.customer_id) if e.customer_id else None
            actor=refresh_actor(db,e.actor_id) if not customer else None
            objects=authorize_export(db,e,actor,customer);current=current_data(db,e,objects)
            if fingerprint(current)!=e.source_fingerprint:raise DomainError('stale_export','Sources changed before export',409)
            j.status='running';j.locked_at=utcnow();j.attempts+=1;lease=j.locked_at
            data=e.source_snapshot;fmt=e.format;kind=e.kind
        worker_gate(factory,settings)
        if 'mbl' in data:
            from .mbl import render_template
            from .rendering import MIME
            content,media=render_template(settings,data),MIME['xlsx']
        else:content,media=render_artifact(settings,data,fmt,kind)
        with factory() as db,db.begin():
            worker_write_gate(db,settings);e=locked(db,DocumentExport,e_id);j=locked(db,Job,e.job_id)
            if j.status!='running' or j.locked_at!=lease:return False
            customer=db.get(CustomerIdentity,e.customer_id) if e.customer_id else None
            actor=refresh_actor(db,e.actor_id) if not customer else None
            objects=authorize_export(db,e,actor,customer);current=current_data(db,e,objects)
            if fingerprint(current)!=e.source_fingerprint:raise DomainError('stale_export','Sources changed during rendering',409)
            # Storage is an internal write owned by this authorized exporter; it grants no read capability.
            storage=Actor(e.actor_id,{'attachments.write'},set())
            a=put_file(db,storage,content,'MCSTAR-'+e.kind+'.'+e.format,media,'document_export',e.id)
            e.attachment_id=a.id;e.version+=1;j.status='completed';j.error_code=None
            audit(db,storage,'document.export.completed','document_export',e.id,None,{'attachment_id':a.id,'sha256':a.sha256})
        return True
    except Exception as exc:
        if isinstance(exc,DomainError) and exc.code=='writer_frozen':raise
        if not e_id:raise
        with factory() as db,db.begin():
            worker_write_gate(db,settings)
            e=locked(db,DocumentExport,e_id);j=locked(db,Job,e.job_id)
            if j.status=='pending' or (j.status=='running' and j.locked_at==lease):
                j.status='failed';j.error_code=exc.code if isinstance(exc,DomainError) else 'render_failed'
        return True
