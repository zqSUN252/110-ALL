from io import BytesIO
from decimal import Decimal
from sqlalchemy import select
from openpyxl import Workbook,load_workbook
from app.common import DomainError,require,Actor,audit
from app.models import Receipt,Attachment,Job,User,Grant,WarehouseScope
from app.attachments import put_file,read_file
from app.finance.service import record,locked
from app.cargo.commands import command,object_access
from app.cargo.service import get_receipt


def workbook(rows,headers,fields):
    book=Workbook(write_only=True);sheet=book.create_sheet('Data');sheet.append(headers)
    for row in rows:
        values=[]
        for key in fields:
            value=row.get(key)
            if isinstance(value,str) and value[:1] in ('=','+','-','@'):value="'"+value
            if key in ('kg','cbm') and value is not None:value=Decimal(str(value))
            values.append(value)
        sheet.append(values)
    content=BytesIO();book.save(content)
    check=load_workbook(BytesIO(content.getvalue()),read_only=True,data_only=True)
    count=sum(1 for _ in check.active.iter_rows())-1;check.close()
    if count!=len(rows):raise DomainError('export_verify','Workbook row readback failed',500)
    return content.getvalue()


def export_receipts(db,actor,p):
    from app.cargo.commands import fields,enum
    fields(p,'warehouse_id mode receipt_ids idempotency_key');require(actor,'cargo.export',p['warehouse_id']);mode=enum(p['mode'],('visible','all'),'export_mode')
    if mode=='all':
        @command('cargo.export.queue','warehouse_id mode receipt_ids','cargo.export')
        def queue(db,actor,p):
            j=Job(kind='cargo_export',payload={'warehouse_id':p['warehouse_id'],'actor_id':actor.id},idempotency_key='cargo_export:'+actor.id+':'+p['idempotency_key'])
            db.add(j);db.flush();return {'id':j.id,'warehouse_id':p['warehouse_id'],'status':'pending'}
        return queue(db,actor,p)
    ids=p.get('receipt_ids',[])
    if not isinstance(ids,list) or not 1<=len(ids)<=200 or len(set(ids))!=len(ids):raise DomainError('visible_selection','Select 1..200 distinct visible receipts')
    rows=[]
    for identity in ids:
        r=get_receipt(db,actor,identity);require(actor,'cargo.export',r['warehouse_id'])
        if r['warehouse_id']!=p['warehouse_id']:raise DomainError('export_scope','Visible export is scoped to selected warehouse',403)
        rows.append(r)
    content=workbook(rows,['Receipt','Received','Transport','Item','PCS','KG','CBM','Destination','Storage'],['reference','received_on','transport','item_zh','pieces','kg','cbm','destination','storage_location'])
    @command('cargo.export.visible','warehouse_id mode receipt_ids','cargo.export')
    def persist(db,actor,p):
        a=put_file(db,actor,content,'receipts.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','cargo_export',p['warehouse_id'])
        return {'id':a.id,'attachment_id':a.id,'warehouse_id':p['warehouse_id'],'rows':len(rows),'sources':[{'receipt_id':r['id'],'version':r['version']} for r in rows]}
    return persist(db,actor,p)


def export_inbound(db,actor,p):
    from app.cargo.commands import fields
    from .analytics import inbound
    fields(p,'warehouse_id on salesperson idempotency_key');require(actor,'cargo.export',p['warehouse_id'])
    data=inbound(db,actor,p.get('on'),p['warehouse_id'],p.get('salesperson'))
    content=workbook(data['rows'],['Receipt','Destination','Salesperson','PCS','KG','CBM'],['reference','destination','salesperson','pieces','kg','cbm'])
    @command('cargo.export.inbound','warehouse_id on salesperson','cargo.export')
    def persist(db,actor,p):
        a=put_file(db,actor,content,'inbound.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','cargo_export',p['warehouse_id'])
        return {'id':a.id,'attachment_id':a.id,'warehouse_id':p['warehouse_id'],'rows':len(data['rows']),'totals':data['totals'],'sources':data['rows']}
    return persist(db,actor,p)


def export_once(factory,settings):
    from datetime import timedelta
    from app.migration.operations import worker_gate,worker_write_gate
    from app.channels.worker import actor_now
    from app.models import utcnow
    worker_gate(factory,settings)
    identity=lease=None
    try:
        with factory() as db,db.begin():
            worker_write_gate(db,settings)
            job=db.scalar(select(Job).where(Job.kind=='cargo_export',((Job.status=='pending')|((Job.status=='running')&(Job.locked_at<utcnow()-timedelta(minutes=5))))).order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
            if not job:return {'status':'idle'}
            payload=job.payload;actor=actor_now(db,payload['actor_id'])
            require(actor,'cargo.export',payload['warehouse_id']);require(actor,'cargo.read',payload['warehouse_id'])
            job.status='running';job.locked_at=utcnow();job.attempts+=1;identity=job.id;lease=job.locked_at
            receipts=list(db.scalars(select(Receipt).where(Receipt.warehouse_id==payload['warehouse_id']).order_by(Receipt.received_on,Receipt.id)))
            rows=[record(r) for r in receipts];sources={r.id:r.version for r in receipts}
        # Rendering occurs with no held mutation lock; freeze/generation is
        # rechecked before and after it, never inferred from time alone.
        worker_gate(factory,settings)
        content=workbook(rows,['Receipt','Received','Transport','Item','PCS','KG','CBM','Destination','Storage'],['reference','received_on','transport','item_zh','pieces','kg','cbm','destination','storage_location'])
        with factory() as db,db.begin():
            worker_write_gate(db,settings);job=locked(db,Job,identity)
            if job.status!='running' or job.locked_at!=lease:return {'id':identity,'status':'lease_lost'}
            actor=actor_now(db,payload['actor_id'])
            require(actor,'cargo.export',payload['warehouse_id']);require(actor,'cargo.read',payload['warehouse_id'])
            current={r.id:r.version for r in db.scalars(select(Receipt).where(Receipt.warehouse_id==payload['warehouse_id']))}
            if current!=sources:raise DomainError('stale_export','Receipt sources changed during export',409)
            a=put_file(db,actor,content,'receipts-all.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','cargo_export',payload['warehouse_id'])
            job.status='complete';job.payload=payload|{'attachment_id':a.id,'count':len(rows)};job.version+=1
            audit(db,actor,'cargo.export.complete','job',job.id,None,job.payload)
            return {'id':job.id,'status':'complete','attachment_id':a.id,'rows':len(rows)}
    except Exception as exc:
        if isinstance(exc,DomainError) and exc.code=='writer_frozen':raise
        if not identity:raise
        with factory() as db,db.begin():
            worker_write_gate(db,settings);job=locked(db,Job,identity)
            if job.status=='pending' or (job.status=='running' and job.locked_at==lease):
                job.status='failed';job.error_code=exc.code if isinstance(exc,DomainError) else 'render_failed';job.version+=1
        return {'id':identity,'status':'failed','error_code':exc.code if isinstance(exc,DomainError) else 'render_failed'}
