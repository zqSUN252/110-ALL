from sqlalchemy import select
from app.common import DomainError,require,check_version,json_value
from app.models import ImportBatch,CargoSourceRow,PackingDocument,DocumentRevision,Ticket,Receipt,Shipment
from app.finance import service as finance
from app.bank.service import attachment_for_actor
from app.attachments import read_file
from app.cargo.commands import command,object_access,enum,fields,visible
from .parser import parse
from . import service


def batch_json(db,b):
    return finance.record(b)|{'rows':[finance.record(r) for r in db.scalars(select(CargoSourceRow).where(CargoSourceRow.batch_id==b.id).order_by(CargoSourceRow.position))]}


def get_preview(db,actor,identity):
    return batch_json(db,object_access(db,actor,ImportBatch,identity,'packing.read'))


def preview_import(db,actor,p):
    fields(p,'warehouse_id attachment_id kind mapping idempotency_key')
    require(actor,'packing.import',p['warehouse_id'])
    attachment=attachment_for_actor(db,actor,p['attachment_id'])
    rows,mapping,issues=parse(read_file(db.info['settings'],attachment),p.get('mapping'))
    # File parsing precedes mutation locks, never calls external models.
    @command('cargo.import.preview','warehouse_id attachment_id kind mapping','packing.import')
    def persist(db,actor,p):
        attachment_for_actor(db,actor,p['attachment_id'])
        kind=enum(p['kind'],('packing','f6'),'import_kind')
        b=ImportBatch(warehouse_id=p['warehouse_id'],attachment_id=attachment.id,kind=kind,mapping=mapping,issues=issues)
        db.add(b);db.flush()
        db.add_all([CargoSourceRow(batch_id=b.id,**r) for r in rows]);db.flush();return batch_json(db,b)
    return persist(db,actor,p)


def selected_rows(db,b,selection):
    all_rows={r.id:r for r in db.scalars(select(CargoSourceRow).where(CargoSourceRow.batch_id==b.id).order_by(CargoSourceRow.position))}
    if not isinstance(selection,list) or not selection or len({r['row_id'] for r in selection})!=len(selection):raise DomainError('import_selection','Explicit unique source rows required')
    result=[]
    for item in selection:
        fields(item,'row_id values native_receipt_id')
        r=all_rows.get(item['row_id'])
        if not r or not r.eligible:raise DomainError('source_row','Select eligible rows from this preview')
        changes=item.get('values',{});fields(changes,service.TICKET_FIELDS)
        data={k:getattr(r,k) for k in ('receipt','mark','contact_raw','destination','address','received_on','pieces','kg','cbm','freight_usd','clearing_ngn')}
        data['item_zh']=r.item
        data.update(changes)
        if item.get('native_receipt_id'):data['native_receipt_id']=item['native_receipt_id']
        result.append((r,data))
    result.sort(key=lambda pair:pair[0].position);return result


@command('cargo.import.commit','id expected_version selection excluded_rows shipment_id reference uploaded_on','packing.import',ImportBatch)
def commit_import(db,actor,p):
    b=object_access(db,actor,ImportBatch,p['id'],'packing.import');check_version(b,p.get('expected_version'))
    if b.status!='preview':raise DomainError('already_imported','Preview already committed',409)
    rows=selected_rows(db,b,p['selection'])
    eligible=set(db.scalars(select(CargoSourceRow.id).where(CargoSourceRow.batch_id==b.id,CargoSourceRow.eligible.is_(True))))
    excluded=p.get('excluded_rows',[])
    if any(set(x)!={'row_id','reason'} or not str(x['reason']).strip() for x in excluded):raise DomainError('exclude_reason','Explicit reasons required for excluded source cargo')
    if eligible!={r.id for r,_ in rows}|{r['row_id'] for r in excluded} or {r.id for r,_ in rows}&{r['row_id'] for r in excluded}:raise DomainError('source_coverage','Every eligible original row must be selected or explicitly excluded')
    if b.kind=='f6':
        # External observations do not manufacture another physical inbound event.
        for r,data in rows:
            requested=next(item.get('values',{}) for item in p['selection'] if item['row_id']==r.id)
            allowed='receipt mark item_zh contact_raw destination address received_on pieces kg cbm freight_usd clearing_ngn'
            fields(requested,allowed)
            converted=service.ticket_values(requested)
            before={k:getattr(r,'item' if k=='item_zh' else k) for k in converted}
            for key,value in converted.items():setattr(r,'item' if key=='item_zh' else key,value)
            if requested:r.provenance=r.provenance|{'manual_correction':{'actor_id':actor.id,'original_values':json_value(before),'confirmed_values':json_value(converted)}};r.version+=1
            if data.get('native_receipt_id'):
                native=object_access(db,actor,Receipt,data['native_receipt_id'],'cargo.read')
                if native.warehouse_id!=b.warehouse_id:raise DomainError('receipt_scope','Native receipt warehouse differs',403)
                r.native_receipt_id=native.id
        b.status='committed';b.version+=1;db.flush();return batch_json(db,b)
    s=object_access(db,actor,Shipment,p['shipment_id'],'packing.manage')
    if s.warehouse_id!=b.warehouse_id:raise DomainError('import_scope','Shipment and file warehouse differ',403)
    d=PackingDocument(shipment_id=s.id,warehouse_id=s.warehouse_id,reference=p['reference'],uploaded_on=finance.day(p['uploaded_on']))
    db.add(d);db.flush()
    for r,data in rows:
        t=service.new_ticket(db,actor,d,data,allow_unresolved=True);r.ticket_id=t.id
    b.document_id=d.id;b.status='committed';b.version+=1
    db.add(DocumentRevision(document_id=d.id,batch_id=b.id,target_version=d.version,mapping={r.id:r.ticket_id for r,_ in rows},difference={'excluded_rows':excluded},actor_id=actor.id))
    db.flush();return service.document_json(db,d)|{'import_id':b.id}


def replacement_preview(db,actor,identity,batch_id,mapping):
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    d=object_access(db,actor,PackingDocument,identity,'packing.manage');b=object_access(db,actor,ImportBatch,batch_id,'packing.import')
    if d.warehouse_id!=b.warehouse_id or b.kind!='packing' or b.status!='preview':raise DomainError('replacement_scope','Preview must be unused packing source in same warehouse')
    if not isinstance(mapping,list) or not mapping:raise DomainError('mapping_required','Explicit old/new row identity mapping required')
    source={r.id:r for r in db.scalars(select(CargoSourceRow).where(CargoSourceRow.batch_id==b.id,CargoSourceRow.eligible.is_(True)))}
    tickets={t.id:t for t in db.scalars(select(Ticket).where(Ticket.document_id==d.id,Ticket.state=='active'))}
    mapped_source=set();mapped_tickets=set();changes=[]
    for row in mapping:
        fields(row,'row_id ticket_id values discount_total_ngn')
        rid=row.get('row_id');tid=row.get('ticket_id')
        if not rid or rid not in source or rid in mapped_source:raise DomainError('row_mapping','Each new source row needs one explicit mapping')
        if tid and (tid not in tickets or tid in mapped_tickets):raise DomainError('ticket_mapping','Old ticket must belong to document and map once')
        mapped_source.add(rid)
        if tid:mapped_tickets.add(tid)
        r=source[rid];t=tickets.get(tid);data=selected_rows(db,b,[{'row_id':rid,'values':row.get('values',{})}])[0][1]
        data=service.ticket_values(data)
        old={k:finance.record(t)[k] for k in data} if t else None
        impact=None
        if t and t.invoice_id:
            service.financial_access(db,actor,t)
            usd=data.get('freight_usd',t.freight_usd);ngn=data.get('clearing_ngn',t.clearing_ngn);rate=data.get('ngn_per_usd',t.ngn_per_usd)
            if usd is None and ngn is None:raise DomainError('replacement_price','Billed ticket needs explicit reviewed charges')
            if usd and not rate:raise DomainError('fx_missing','Explicit settlement rate required')
            from types import SimpleNamespace
            from app.finance.rounding import cargo_basis
            proposed=SimpleNamespace(**{c.name:getattr(t,c.name) for c in t.__table__.columns})
            for k,v in data.items():setattr(proposed,k,v)
            proposed.version=t.version+1
            if db.scalar(select(service.Arrival.id).where(service.Arrival.ticket_id==t.id)):
                basis=cargo_basis(db,proposed)
                impact=finance.adjustment_preview(db,actor,t.invoice_id,basis['amount_ngn'],row.get('discount_total_ngn'))
                impact['new_rounding_adjustment_ngn']=basis['rounding_adjustment_ngn'];impact['rounding_basis']=basis
            else:impact=finance.adjustment_preview(db,actor,t.invoice_id,str(service.invoice_amount(usd or Decimal(0),ngn or Decimal(0),rate)),row.get('discount_total_ngn'))
        changes.append({'row_id':rid,'ticket_id':tid,'ticket_version':t.version if t else None,'old':old,'new':json_value(data),'finance_preview':impact,'discount_total_ngn':row.get('discount_total_ngn')})
    if mapped_source!=set(source) or mapped_tickets!=set(tickets):raise DomainError('mapping_coverage','All new eligible rows and existing active tickets must map explicitly; void removed tickets separately with reviewed financial impact')
    return {'id':d.id,'version':d.version,'batch_id':b.id,'batch_version':b.version,'changes':changes}


from decimal import Decimal,ROUND_HALF_UP


@command('document.replace','id expected_version batch_id mapping preview reason','packing.manage',PackingDocument,financial=True)
def replace_document(db,actor,p):
    d=object_access(db,actor,PackingDocument,p['id'],'packing.manage');check_version(d,p.get('expected_version'))
    preview=replacement_preview(db,actor,d.id,p['batch_id'],p['mapping'])
    if preview!=p.get('preview'):raise DomainError('preview_changed','Replacement difference changed',409,preview)
    if not str(p.get('reason','')).strip():raise DomainError('reason_required','Replacement reason required')
    b=finance.locked(db,ImportBatch,p['batch_id'])
    for change in preview['changes']:
        data=dict(change['new'])
        if change['ticket_id']:
            t=finance.locked(db,Ticket,change['ticket_id'])
            data.update(finance_preview=change['finance_preview'],reason=p['reason'],idempotency_key=p['idempotency_key']+':'+t.id)
            if change['discount_total_ngn'] is not None:data['discount_total_ngn']=change['discount_total_ngn']
            service.edit_ticket(db,actor,t,data)
        else:t=service.new_ticket(db,actor,d,data,allow_unresolved=True)
        r=finance.locked(db,CargoSourceRow,change['row_id']);r.ticket_id=t.id;t.position=r.position
    d.version+=1;b.version+=1;b.status='committed';b.document_id=d.id
    db.add(DocumentRevision(document_id=d.id,batch_id=b.id,target_version=d.version,mapping={c['row_id']:c['ticket_id'] for c in preview['changes']},difference=preview,actor_id=actor.id))
    db.flush();return service.document_json(db,d)


def list_f6(db,actor,warehouse_id=None,query=None,sort='receipt',limit=50,offset=0):
    require(actor,'cargo.read')
    enum(sort,('receipt','received_on','destination','mark'),'sort')
    if not 1<=limit<=200 or offset<0:raise DomainError('pagination','Limit 1..200 required')
    q=select(CargoSourceRow).join(ImportBatch,ImportBatch.id==CargoSourceRow.batch_id).where(ImportBatch.kind=='f6',ImportBatch.status=='committed',CargoSourceRow.eligible.is_(True))
    q=visible(q,ImportBatch,actor)
    if warehouse_id:require(actor,'cargo.read',warehouse_id);q=q.where(ImportBatch.warehouse_id==warehouse_id)
    if query:q=q.where(CargoSourceRow.receipt.icontains(query,autoescape=True))
    return [finance.record(r) for r in db.scalars(q.order_by(getattr(CargoSourceRow,sort),CargoSourceRow.id).limit(limit).offset(offset))]
