from io import BytesIO
from types import SimpleNamespace
from sqlalchemy import select
from openpyxl import load_workbook
from app.common import DomainError,require,check_version
from app.models import MBLRecord,MBLAttachment,DocumentTemplate,Shipment,Attachment,Job,DocumentExport
from app.finance import service as finance
from app.attachments import read_file
from app.cargo.commands import fields,integer
from .access import command,own_upload,fingerprint
from .rendering import literal,MIME

FIELDS='number shipper consignee notify_party vessel voyage port_loading port_discharge final_destination description marks packages gross_kg cbm freight_terms booking_no sailing_on'
KINDS=('BO','SI','SO','DN')


def access(db,actor,identity,capability='documents.read'):
    row=finance.locked(db,MBLRecord,identity);s=finance.locked(db,Shipment,row.shipment_id);require(actor,capability,s.warehouse_id);return row


@command('mbl.save','shipment_id expected_version '+FIELDS,'documents.manage')
def save_mbl(db,actor,p):
    row=db.scalar(select(MBLRecord).where(MBLRecord.shipment_id==p['shipment_id']).with_for_update())
    if row:check_version(row,p.get('expected_version'));row.version+=1
    else:
        if p.get('expected_version')!=0:raise DomainError('stale_version','New MBL requires version 0',409)
        row=MBLRecord(shipment_id=p['shipment_id']);db.add(row)
    for key in FIELDS.split():
        if key not in p:continue
        value=p[key]
        if key=='sailing_on' and value is not None:value=finance.day(value)
        elif key in ('gross_kg','cbm') and value is not None:value=finance.decimal(value,6)
        elif key=='packages' and value is not None:value=integer(value)
        elif value is not None and (not isinstance(value,str) or len(value)>20000):raise DomainError('mbl_field','MBL text must be bounded text')
        setattr(row,key,value)
    db.flush();return finance.record(row)


@command('template.configure','kind expected_version attachment_id field_mapping','documents.templates')
def configure_template(db,actor,p):
    if p['kind'] not in KINDS:raise DomainError('template_kind','Choose BO, SI, SO or DN')
    a=own_upload(db,actor,p['attachment_id']);content=read_file(db.info['settings'],a)
    try:book=load_workbook(BytesIO(content),data_only=False,keep_links=False)
    except Exception:raise DomainError('template_file','An actual XLSX template is required') from None
    if not isinstance(p['field_mapping'],dict) or not p['field_mapping']:raise DomainError('template_mapping','Actual template field/cell mapping required')
    if len(set(p['field_mapping'].values()))!=len(p['field_mapping']):raise DomainError('template_mapping','Each source field needs a distinct template cell')
    for key,target in p['field_mapping'].items():
        if key not in FIELDS.split() or not isinstance(target,str) or '!' not in target:raise DomainError('template_mapping','Map typed MBL fields to Sheet!A1 cells')
        sheet,cell=target.rsplit('!',1)
        import re
        if sheet not in book.sheetnames or not re.fullmatch(r'[A-Z]{1,3}[1-9]\d{0,4}',cell):raise DomainError('template_mapping','Mapped cell is not in the actual template')
    row=db.scalar(select(DocumentTemplate).where(DocumentTemplate.kind==p['kind']).with_for_update())
    if row:check_version(row,p.get('expected_version'));row.version+=1
    else:
        if p.get('expected_version')!=0:raise DomainError('stale_version','New template requires version 0',409)
        row=DocumentTemplate(kind=p['kind']);db.add(row)
    row.attachment_id=a.id;row.field_mapping=p['field_mapping'];row.active=True;db.flush();return finance.record(row)


def snapshot(db,identity,kind):
    row=finance.locked(db,MBLRecord,identity);s=db.get(Shipment,row.shipment_id)
    template=db.scalar(select(DocumentTemplate).where(DocumentTemplate.kind==kind,DocumentTemplate.active.is_(True)))
    if not template:raise DomainError('template_missing','Original '+kind+' template has not been configured',409)
    a=db.get(Attachment,template.attachment_id)
    return {'mbl':finance.record(row),'shipment_version':s.version,'template':finance.record(template),'template_attachment':finance.record(a)}


def render_template(settings,data):
    source=read_file(settings,SimpleNamespace(**data['template_attachment']));book=load_workbook(BytesIO(source),data_only=False,keep_links=False)
    for key,target in data['template']['field_mapping'].items():
        sheet,cell=target.rsplit('!',1);value=data['mbl'][key]
        if key in ('gross_kg','cbm') and value is not None:
            from decimal import Decimal
            value=Decimal(value)
        literal(book[sheet][cell],value)
    output=BytesIO();book.save(output);content=output.getvalue();check=load_workbook(BytesIO(content),data_only=False)
    for key,target in data['template']['field_mapping'].items():
        sheet,cell=target.rsplit('!',1)
        if isinstance(data['mbl'][key],str) and check[sheet][cell].data_type=='f':raise DomainError('template_verify','Source field became a formula',500)
    return content


def generate(db,actor,identity,kind):
    access(db,actor,identity,'documents.export');return render_template(db.info['settings'],snapshot(db,identity,kind))


@command('mbl.export','id expected_version kind','documents.export','mbl_id')
def queue(db,actor,p):
    row=access(db,actor,p['id'],'documents.export');check_version(row,p.get('expected_version'));data=snapshot(db,row.id,p['kind'])
    j=Job(kind='document_export',payload={'actor_id':actor.id},idempotency_key='mbl_export:'+actor.id+':'+p['idempotency_key']);db.add(j);db.flush()
    e=DocumentExport(job_id=j.id,actor_id=actor.id,format='xlsx',kind=p['kind'],mbl_id=row.id,source_snapshot=data,source_fingerprint=fingerprint(data));db.add(e);db.flush()
    return {'id':e.id,'export_id':e.id,'mbl_id':row.id,'version':e.version,'status':j.status}


@command('mbl.attach','id expected_version attachment_id kind','documents.manage','mbl_id')
def attach(db,actor,p):
    row=access(db,actor,p['id'],'documents.manage');check_version(row,p.get('expected_version'))
    if p['kind'] not in KINDS:raise DomainError('attachment_kind','Choose BO, SI, SO or DN')
    a=own_upload(db,actor,p['attachment_id']);row.version+=1
    link=MBLAttachment(mbl_id=row.id,attachment_id=a.id,kind=p['kind'],source_version=row.version);db.add(link);db.flush()
    return finance.record(link)|{'mbl_version':row.version}


def get(db,actor,identity):
    row=access(db,actor,identity)
    return finance.record(row)|{'attachments':[finance.record(a) for a in db.scalars(select(MBLAttachment).where(MBLAttachment.mbl_id==row.id))],'templates':{kind:bool(db.scalar(select(DocumentTemplate.id).where(DocumentTemplate.kind==kind,DocumentTemplate.active.is_(True)))) for kind in KINDS}}


def original(db,settings,actor,identity,attachment_id):
    access(db,actor,identity)
    if not db.scalar(select(MBLAttachment.id).where(MBLAttachment.mbl_id==identity,MBLAttachment.attachment_id==attachment_id)):raise DomainError('not_found','Original document not found',404)
    a=finance.locked(db,Attachment,attachment_id);return read_file(settings,a),a.media_type
