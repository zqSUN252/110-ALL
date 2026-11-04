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
    with factory() as db,db.begin():
        job=db.scalar(select(Job).where(Job.kind=='cargo_export',Job.status=='pending').order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
        if not job:return {'status':'idle'}
        job.status='running';job.attempts+=1;identity=job.id;payload=job.payload
    with factory() as db,db.begin():
        db.info['settings']=settings;user=db.get(User,payload['actor_id'])
        actor=Actor(payload['actor_id'],set(db.scalars(select(Grant.capability).where(Grant.user_id==payload['actor_id']))),set(db.scalars(select(WarehouseScope.warehouse_id).where(WarehouseScope.user_id==payload['actor_id']))))
        job=locked(db,Job,identity)
        try:
            if not user or not user.active:raise DomainError('inactive','Actor inactive',403)
            require(actor,'cargo.export',payload['warehouse_id']);require(actor,'cargo.read',payload['warehouse_id'])
        except DomainError:job.status='failed';job.error_code='authorization_changed';return {'id':identity,'status':'failed'}
        rows=[record(r) for r in db.scalars(select(Receipt).where(Receipt.warehouse_id==payload['warehouse_id']).order_by(Receipt.received_on,Receipt.id))]
    # File rendering occurs with no held mutation lock.
    content=workbook(rows,['Receipt','Received','Transport','Item','PCS','KG','CBM','Destination','Storage'],['reference','received_on','transport','item_zh','pieces','kg','cbm','destination','storage_location'])
    with factory() as db,db.begin():
        db.info['settings']=settings;job=locked(db,Job,identity)
        actor=Actor(payload['actor_id'],set(db.scalars(select(Grant.capability).where(Grant.user_id==payload['actor_id']))),set(db.scalars(select(WarehouseScope.warehouse_id).where(WarehouseScope.user_id==payload['actor_id']))))
        require(actor,'cargo.export',payload['warehouse_id']);require(actor,'cargo.read',payload['warehouse_id'])
        a=put_file(db,actor,content,'receipts-all.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','cargo_export',payload['warehouse_id'])
        job.status='complete';job.payload=payload|{'attachment_id':a.id,'count':len(rows)};job.version+=1
        audit(db,actor,'cargo.export.complete','job',job.id,None,job.payload)
        return {'id':job.id,'status':'complete','attachment_id':a.id,'rows':len(rows)}
