from decimal import Decimal,ROUND_HALF_UP
from datetime import date
from sqlalchemy import select,func
from app.common import DomainError,require,check_version,json_value
from app.models import Receipt,ReceiptSpec,StockEvent,ReceiptReview,DispatchBatch,Shipment,QuoteSnapshot,Attachment
from app.finance.service import locked,record,decimal,day
from app.attachments import put_file,read_file
from .commands import command,member,object_access,integer,fields,enum,port,bump,visible

RECEIPT_FIELDS='party_id received_on transport origin goods_class destination destination_warehouse_id storage_location item_zh item_en customer_phone supplier_phone salesperson operator_name courier_company courier_number declaration_required'
TRANSPORTS=('SEA','AIR','EXPRESS','WAREHOUSE')


def specifications(rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=200:raise DomainError('specs_required','Provide 1 to 200 specifications')
    result=[]
    for position,r in enumerate(rows):
        fields(r,'length width height unit pieces kg packing')
        unit=enum(r['unit'],('MM','CM','M'),'dimension_unit')
        lengths=[decimal(r[k],6,True) for k in ('length','width','height')]
        pieces=integer(r['pieces'],True);kg=decimal(r['kg'],6)
        divisor={'MM':Decimal(1000000000),'CM':Decimal(1000000),'M':Decimal(1)}[unit]
        cbm=(lengths[0]*lengths[1]*lengths[2]*pieces/divisor).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP)
        result.append(dict(position=position,length=lengths[0],width=lengths[1],height=lengths[2],unit=unit,pieces=pieces,kg=kg,cbm=cbm,packing=r.get('packing')))
    return result


def remaining(db,r):
    values=db.execute(select(func.coalesce(func.sum(StockEvent.pieces),0),func.coalesce(func.sum(StockEvent.kg),0),func.coalesce(func.sum(StockEvent.cbm),0)).where(StockEvent.receipt_id==r.id)).one()
    return dict(pieces=int(values[0]),kg=f'{values[1]:.6f}',cbm=f'{values[2]:.6f}')


def receipt_json(db,r):
    return record(r)|{'specs':[record(s) for s in db.scalars(select(ReceiptSpec).where(ReceiptSpec.receipt_id==r.id,ReceiptSpec.revision==r.spec_revision).order_by(ReceiptSpec.position))],
        'remaining':remaining(db,r),'quote':record(db.get(QuoteSnapshot,r.quote_id)) if r.quote_id else None,'actual_release_at':None,
        'events':[record(e) for e in db.scalars(select(StockEvent).where(StockEvent.receipt_id==r.id).order_by(StockEvent.created_at,StockEvent.id))],
        'attachments':[record(a) for a in db.scalars(select(Attachment).where(Attachment.owner_type=='receipt',Attachment.owner_id==r.id).order_by(Attachment.created_at))]}


def get_receipt(db,actor,identity):return receipt_json(db,object_access(db,actor,Receipt,identity,'cargo.read'))


def values(p):
    result={k:p[k] for k in RECEIPT_FIELDS.split() if k in p}
    if 'transport' in result:enum(result['transport'],TRANSPORTS,'transport')
    if 'origin' in result and result['origin'] is not None:enum(result['origin'],('GZ','HK'),'origin')
    if 'received_on' in result:result['received_on']=day(result['received_on'])
    if 'destination' in result:result['destination']=port(result['destination'])
    if 'declaration_required' in result:
        if result['declaration_required'] not in (True,False,None):raise DomainError('invalid_declaration','Use true, false or null')
        result['declaration_required']=result['declaration_required'] is True
        result['declaration_source']='explicit_cleared' if p['declaration_required'] is None else 'explicit'
    return result


def add_specs(db,r,rows):
    db.add_all([ReceiptSpec(receipt_id=r.id,revision=r.spec_revision,**s) for s in rows])
    r.pieces=sum(s['pieces'] for s in rows);r.kg=sum((s['kg'] for s in rows),Decimal(0));r.cbm=sum((s['cbm'] for s in rows),Decimal(0))


@command('receipt.create','warehouse_id series number specs '+RECEIPT_FIELDS,'cargo.manage')
def create_receipt(db,actor,p):
    member(db,actor,p.get('party_id'),p['warehouse_id'],'cargo.manage')
    rows=specifications(p['specs']);number=integer(p['number'],True)
    if not str(p['series']).strip():raise DomainError('series_required','Receipt series required')
    if db.scalar(select(Receipt.id).where(Receipt.series==p['series'],Receipt.warehouse_id!=p['warehouse_id'])):raise DomainError('series_scope','Receipt series belongs to a different warehouse; select its own series',409)
    r=Receipt(warehouse_id=p['warehouse_id'],series=p['series'],number=number,reference=f"{p['series']}{number}",pieces=0,kg=0,cbm=0,**values(p))
    db.add(r);db.flush();add_specs(db,r,rows)
    db.add(StockEvent(receipt_id=r.id,warehouse_id=r.warehouse_id,kind='inbound',occurred_on=r.received_on,pieces=r.pieces,kg=r.kg,cbm=r.cbm,actor_id=actor.id))
    from app.pricing.service import refresh_quote
    refresh_quote(db,r)
    db.flush();return receipt_json(db,r)


@command('receipt.update','id expected_version '+RECEIPT_FIELDS,'cargo.manage',Receipt)
def update_receipt(db,actor,p):
    r=object_access(db,actor,Receipt,p['id'],'cargo.manage');check_version(r,p.get('expected_version'))
    removing=r.id in db.info.get('hold_removing',set()) and r.state=='merged' and set(p)<= {'id','expected_version','customer_phone','idempotency_key'}
    if r.state!='active' and not removing:raise DomainError('receipt_void','Recover receipt before editing',409)
    if 'party_id' in p:member(db,actor,p['party_id'],r.warehouse_id,'cargo.manage')
    if p.get('destination_warehouse_id'):require(actor,'cargo.manage',p['destination_warehouse_id'])
    for k,v in values(p).items():setattr(r,k,v)
    if 'item_en' in p:r.english_manual_version+=1
    bump(r)
    if any(k in p for k in ('destination','transport','origin','goods_class','received_on')):
        from app.pricing.service import refresh_quote
        refresh_quote(db,r)
    db.flush();return receipt_json(db,r)


@command('receipt.repack','id expected_version specs reason source_receipt_ids occurred_on','cargo.manage',Receipt)
def repack(db,actor,p):
    r=object_access(db,actor,Receipt,p['id'],'cargo.manage');check_version(r,p.get('expected_version'))
    if r.state!='active' or not str(p.get('reason','')).strip():raise DomainError('repack_reason','Active receipt and explicit reason required')
    if p.get('source_receipt_ids'):raise DomainError('merge_requires_transfer','Use explicit merge command for quantity transfer')
    specs=specifications(p['specs']);old=(r.pieces,r.kg,r.cbm);balance=remaining(db,r)
    new=(sum(s['pieces'] for s in specs),sum(s['kg'] for s in specs),sum(s['cbm'] for s in specs))
    if any(Decimal(str(balance[k]))+new[i]-old[i]<0 for i,k in enumerate(('pieces','kg','cbm'))):raise DomainError('stock_remaining','Repack would reduce below already dispatched stock',409)
    r.spec_revision+=1;add_specs(db,r,specs);bump(r)
    from app.models import Ticket
    linked=db.scalar(select(Ticket).where(Ticket.native_receipt_id==r.id))
    if linked:
        from .source_allocations import reservation
        reservation(db,linked)
    db.add(StockEvent(receipt_id=r.id,warehouse_id=r.warehouse_id,kind='repack',occurred_on=day(p['occurred_on']),pieces=new[0]-old[0],kg=new[1]-old[1],cbm=new[2]-old[2],actor_id=actor.id,reason=p['reason']))
    from app.pricing.service import refresh_quote
    refresh_quote(db,r);db.flush();return receipt_json(db,r)


@command('receipt.review','id expected_version comment decision','cargo.review',Receipt)
def review_receipt(db,actor,p):
    r=object_access(db,actor,Receipt,p['id'],'cargo.review');check_version(r,p.get('expected_version'))
    decision=enum(p.get('decision','approved'),('approved','rejected'),'decision')
    db.add(ReceiptReview(receipt_id=r.id,target_version=r.version,actor_id=actor.id,decision=decision,comment=p.get('comment')))
    r.reviewed_version=r.version;r.review_state=decision;r.version+=1;db.flush();return receipt_json(db,r)


@command('receipt.state','id expected_version state reason','cargo.manage',Receipt)
def receipt_state(db,actor,p):
    r=object_access(db,actor,Receipt,p['id'],'cargo.manage');check_version(r,p.get('expected_version'))
    if r.state=='merged':raise DomainError('merged_source','Merged source remains immutable transfer history',409)
    state=enum(p['state'],('void','active'),'state')
    if not str(p.get('reason','')).strip():raise DomainError('reason_required','Explicit reason required')
    r.state=state;bump(r);db.flush();return receipt_json(db,r)


@command('cargo.dispatch','shipment_id rows occurred_on reference','cargo.dispatch')
def dispatch(db,actor,p):
    s=object_access(db,actor,Shipment,p['shipment_id'],'cargo.dispatch')
    if s.state=='void':raise DomainError('shipment_void','Shipment is void',409)
    rows=p['rows']
    if not isinstance(rows,list) or not 1<=len(rows)<=200 or len({r['receipt_id'] for r in rows})!=len(rows):raise DomainError('dispatch_rows','Select distinct receipts, maximum 200')
    checked=[]
    for row in sorted(rows,key=lambda r:r['receipt_id']):
        fields(row,'receipt_id expected_version pieces kg cbm')
        r=object_access(db,actor,Receipt,row['receipt_id'],'cargo.dispatch');check_version(r,row.get('expected_version'))
        if r.state!='active' or r.warehouse_id!=s.warehouse_id or r.transport!=s.transport:raise DomainError('dispatch_scope','Active receipt warehouse and transport must match shipment',409)
        q={'pieces':integer(row['pieces'],True),'kg':decimal(row['kg'],6),'cbm':decimal(row['cbm'],6)};balance=remaining(db,r)
        if any(q[k]>Decimal(str(balance[k])) for k in q):raise DomainError('stock_remaining','Dispatch exceeds remaining stock',409)
        if q['pieces']==balance['pieces'] and any(q[k]!=Decimal(balance[k]) for k in ('kg','cbm')):raise DomainError('stock_remainder','Final pieces must consume exact remaining KG and CBM')
        checked.append((r,q))
    b=DispatchBatch(shipment_id=s.id,warehouse_id=s.warehouse_id,occurred_on=day(p['occurred_on']),actor_id=actor.id,reference=p.get('reference'))
    db.add(b);db.flush();result=[]
    for r,q in checked:
        db.add(StockEvent(receipt_id=r.id,warehouse_id=r.warehouse_id,kind='outbound',occurred_on=b.occurred_on,pieces=-q['pieces'],kg=-q['kg'],cbm=-q['cbm'],actor_id=actor.id,batch_id=b.id))
        bump(r);result.append({'receipt_id':r.id,**json_value(q)})
    s.state='dispatched';s.version+=1;db.flush();return record(b)|{'rows':result,'actual_release_at':None}


def list_receipts(db,actor,*,limit=100,offset=0,receipt=None,client=None,item=None,destination=None,transport=None,warehouse_id=None,state=None):
    from app.models import Party
    require(actor,'cargo.read');q=visible(select(Receipt),Receipt,actor)
    if not 1<=limit<=200 or offset<0:raise DomainError('pagination','Use limit 1..200 and nonnegative offset')
    if warehouse_id:require(actor,'cargo.read',warehouse_id);q=q.where(Receipt.warehouse_id==warehouse_id)
    for field,value in [(Receipt.reference,receipt),(Receipt.item_zh,item),(Receipt.destination,destination)]:
        if value:q=q.where(field.icontains(value,autoescape=True))
    if client:
        from app.models import Party
        q=q.join(Party,Party.id==Receipt.party_id).where(Party.name.icontains(client,autoescape=True))
    if transport:q=q.where(Receipt.transport==transport)
    if state:q=q.where(Receipt.state==state)
    page=list(db.scalars(q.order_by(Receipt.received_on.desc(),Receipt.id).offset(offset).limit(limit)))
    parties=dict(db.execute(select(Party.id,Party.name).where(Party.id.in_([r.party_id for r in page if r.party_id]))).all())
    return [receipt_json(db,r)|{'party_name':parties.get(r.party_id)} for r in page]


def numbering_gaps(db,actor,warehouse_id,series):
    require(actor,'cargo.read',warehouse_id)
    numbers=list(db.scalars(select(Receipt.number).where(Receipt.warehouse_id==warehouse_id,Receipt.series==series).order_by(Receipt.number)))
    return [{'from':a+1,'to':b-1} for a,b in zip(numbers,numbers[1:]) if b>a+1]


@command('receipt.merge','id expected_version sources occurred_on reason','cargo.manage',Receipt)
def merge_receipts(db,actor,p):
    sources=p['sources']
    if not isinstance(sources,list) or not sources or len(sources)>50 or len({s['receipt_id'] for s in sources})!=len(sources):raise DomainError('merge_sources','Distinct source receipts required')
    if p['id'] in {s['receipt_id'] for s in sources}:raise DomainError('merge_self','Cannot merge receipt into itself')
    objects={identity:object_access(db,actor,Receipt,identity,'cargo.manage') for identity in sorted([p['id']]+[s['receipt_id'] for s in sources])}
    target=objects[p['id']];check_version(target,p.get('expected_version'))
    if not str(p.get('reason','')).strip():raise DomainError('merge_reason','Source merge reason required')
    specs=[];transfer=[]
    for identity,r in objects.items():
        if r.state!='active' or r.warehouse_id!=target.warehouse_id or r.party_id!=target.party_id or r.transport!=target.transport:raise DomainError('merge_scope','Merge requires active receipts of same warehouse, confirmed party and transport')
        if identity!=target.id:
            request=next(s for s in sources if s['receipt_id']==identity);fields(request,'receipt_id expected_version');check_version(r,request.get('expected_version'))
            from app.models import Ticket
            # Void tickets remain recoverable document members; merging their
            # source would make every later recovery fail permanently.
            if db.scalar(select(Ticket.id).where(Ticket.native_receipt_id==r.id)):
                raise DomainError('native_source_referenced','Resolve every ticket source reference, including void members, before merging this source receipt',409)
        balance=remaining(db,r)
        if balance!={'pieces':r.pieces,'kg':f'{r.kg:.6f}','cbm':f'{r.cbm:.6f}'}:raise DomainError('merge_partial','Partly dispatched stock requires explicit remaining specifications before merge',409)
        for spec in db.scalars(select(ReceiptSpec).where(ReceiptSpec.receipt_id==r.id,ReceiptSpec.revision==r.spec_revision).order_by(ReceiptSpec.position)):
            specs.append({k:getattr(spec,k) for k in ('length','width','height','unit','pieces','kg','cbm','packing')}|{'position':len(specs)})
        if identity!=target.id:transfer.append(r)
    target.spec_revision+=1;add_specs(db,target,specs);bump(target)
    for source in transfer:
        for receipt,sign,kind in [(source,-1,'merge_out'),(target,1,'merge_in')]:
            db.add(StockEvent(receipt_id=receipt.id,warehouse_id=receipt.warehouse_id,kind=kind,occurred_on=day(p['occurred_on']),pieces=source.pieces*sign,kg=source.kg*sign,cbm=source.cbm*sign,actor_id=actor.id,reason=p['reason'],source_receipt_id=source.id))
        source.state='merged';bump(source)
    from app.pricing.service import refresh_quote
    refresh_quote(db,target);db.flush();return receipt_json(db,target)


@command('receipt.attach','id expected_version attachment_id','cargo.manage',Receipt)
def attach_receipt(db,actor,p):
    from app.bank.service import attachment_for_actor
    r=object_access(db,actor,Receipt,p['id'],'cargo.manage');check_version(r,p.get('expected_version'))
    original=attachment_for_actor(db,actor,p['attachment_id'])
    if not original.media_type.startswith('image/'):raise DomainError('image_required','Cargo photograph must be an image')
    # Independent metadata references identical immutable bytes; never reassign
    # another domain's ownership or overwrite its attachment identity.
    a=Attachment(sha256=original.sha256,object_path=original.object_path,filename=original.filename,media_type=original.media_type,size_bytes=original.size_bytes,created_by=actor.id,owner_type='receipt',owner_id=r.id)
    db.add(a);bump(r);db.flush();return receipt_json(db,r)|{'attachment':record(a)}
