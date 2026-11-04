from datetime import timedelta
import hashlib
import re
from sqlalchemy import select,text
from app.common import DomainError,require,check_version,json_value
from app.models import Ticket,Receipt,Shipment,PackingDocument,TrackingNode,PhysicalRelease,PhysicalPhoto,Arrival,StockEvent,PublicAttempt,CargoSourceRow,ImportBatch,Warehouse,utcnow
from app.finance.service import locked,record
from app.documents.access import command,instant

NODES={'packing_created':('箱单制作','Packing list prepared'),'packing_sent':('箱单已发送','Packing list sent'),'loading':('装载','Loaded'),'departed':('已启运','Departed'),'arrived_port':('到港','Arrived at port'),'customs_cleared':('清关完成','Customs cleared'),'warehouse_arrived':('目的仓到货','Destination warehouse arrival')}


@command('tracking.node','target_type target_id expected_version node mode occurred_at source destination_warehouse_id','tracking.manage')
def set_node(db,actor,p):
    if p['node'] not in NODES or p['target_type'] not in ('ticket','shipment') or p['mode'] not in ('manual','follow'):raise DomainError('node','Choose a supported node and target; physical release is a separate warehouse command')
    if p['target_type']=='shipment' and p['mode']=='follow':raise DomainError('node','Main shipment cannot follow itself')
    if not str(p.get('source','')).strip():raise DomainError('node_source','Actual event source required')
    obj=locked(db,Ticket if p['target_type']=='ticket' else Shipment,p['target_id']);check_version(obj,p.get('expected_version'))
    coverage=None;destination=None
    if p['node']=='warehouse_arrived':
        destination=p.get('destination_warehouse_id')
        if not destination:raise DomainError('warehouse_required','Actual destination warehouse is required')
        locked(db,Warehouse,destination)
        if isinstance(obj,Ticket) and obj.destination_warehouse_id!=destination:raise DomainError('warehouse_scope','Warehouse confirmation must match the actual ticket destination',403)
        targets=[obj] if isinstance(obj,Ticket) else list(db.scalars(select(Ticket).join(PackingDocument,Ticket.document_id==PackingDocument.id).where(PackingDocument.shipment_id==obj.id,PackingDocument.state=='active',Ticket.state=='active',Ticket.destination_warehouse_id==destination).order_by(Ticket.id)))
        coverage={'tickets':[{'ticket_id':t.id,'ticket_version':t.version,'arrivals':[{'id':a.id,'version':a.version} for a in db.scalars(select(Arrival).where(Arrival.ticket_id==t.id).order_by(Arrival.id))]} for t in targets]}
        if p['mode']=='manual' and p.get('occurred_at') and not any(t['arrivals'] for t in coverage['tickets']):raise DomainError('warehouse_coverage','Actual existing arrival batches are required for warehouse confirmation',409)
    key='ticket_id' if isinstance(obj,Ticket) else 'shipment_id'
    query=select(TrackingNode).where(getattr(TrackingNode,key)==obj.id,TrackingNode.node==p['node'])
    if isinstance(obj,Shipment) and p['node']=='warehouse_arrived':query=query.where(TrackingNode.destination_warehouse_id==destination)
    row=db.scalar(query.with_for_update())
    if row:row.version+=1
    else:row=TrackingNode(**{key:obj.id},node=p['node']);db.add(row)
    row.mode=p['mode'];row.occurred_at=instant(p['occurred_at']) if p['mode']=='manual' and p.get('occurred_at') else None
    row.actor_id=actor.id;row.source=p['source'];row.destination_warehouse_id=destination;row.coverage=coverage;obj.version+=1;db.flush()
    return record(row)|{'target_type':p['target_type'],'target_id':obj.id,'target_version':obj.version}


def effective_node(db,t,node):
    own=db.scalar(select(TrackingNode).where(TrackingNode.ticket_id==t.id,TrackingNode.node==node))
    query=select(TrackingNode).where(TrackingNode.shipment_id==db.get(PackingDocument,t.document_id).shipment_id,TrackingNode.node==node)
    if node=='warehouse_arrived':query=query.where(TrackingNode.destination_warehouse_id==t.destination_warehouse_id)
    row=own if own and own.mode=='manual' else db.scalar(query)
    if row and node=='warehouse_arrived' and row.destination_warehouse_id!=t.destination_warehouse_id:return None
    return row


def warehouse_arrivals(db,t):
    """Only immutable existing arrival batches covered by a current applicable warehouse fact."""
    node=effective_node(db,t,'warehouse_arrived')
    if not node or not node.occurred_at or not node.coverage:return []
    member=next((m for m in node.coverage.get('tickets',[]) if m['ticket_id']==t.id),None)
    if not member:return []
    rows=[]
    for source in member['arrivals']:
        a=db.get(Arrival,source['id'])
        if a and a.ticket_id==t.id and a.version==source['version']:rows.append(a)
    return rows


def warehouse_basis(db,t):
    n=effective_node(db,t,'warehouse_arrived');c=effective_node(db,t,'customs_cleared')
    return {'warehouse_node':record(n) if n else None,'customs_node':record(c) if c else None,'covered_arrivals':[record(a) for a in warehouse_arrivals(db,t)]}


def warehouse_preview(db,actor,target_type,target_id,destination_warehouse_id):
    if target_type not in ('shipment','ticket'):raise DomainError('target','Choose shipment or ticket')
    require(actor,'tracking.manage',destination_warehouse_id);require(actor,'tracking.read',destination_warehouse_id)
    obj=locked(db,Ticket if target_type=='ticket' else Shipment,target_id)
    targets=[obj] if isinstance(obj,Ticket) and obj.destination_warehouse_id==destination_warehouse_id else [] if isinstance(obj,Ticket) else list(db.scalars(select(Ticket).join(PackingDocument,Ticket.document_id==PackingDocument.id).where(PackingDocument.shipment_id==obj.id,PackingDocument.state=='active',Ticket.state=='active',Ticket.destination_warehouse_id==destination_warehouse_id)))
    if not targets:raise DomainError('not_found','No source tickets in this destination warehouse',404)
    return json_value({'target_type':target_type,'target_id':obj.id,'expected_version':obj.version,'destination_warehouse_id':destination_warehouse_id,'tickets':[{'ticket_id':t.id,'version':t.version,'receipt':t.receipt,'arrivals':[{'id':a.id,'version':a.version,'occurred_on':a.occurred_on,'pieces':a.pieces,'kg':a.kg,'cbm':a.cbm} for a in db.scalars(select(Arrival).where(Arrival.ticket_id==t.id).order_by(Arrival.id))]} for t in targets]})


def timeline(db,ticket_id):
    t=locked(db,Ticket,ticket_id);d=db.get(PackingDocument,t.document_id)
    own={r.node:r for r in db.scalars(select(TrackingNode).where(TrackingNode.ticket_id==t.id))}
    nodes={}
    for name,labels in NODES.items():
        override=own.get(name);r=effective_node(db,t,name)
        if name=='warehouse_arrived' and r and (r.destination_warehouse_id!=t.destination_warehouse_id or not any(m['ticket_id']==t.id for m in (r.coverage or {}).get('tickets',[]))):r=None
        nodes[name]={'occurred_at':r.occurred_at if r else None,'mode':'manual' if override and override.mode=='manual' else 'follow','labels':{'zh':labels[0],'en':labels[1]},'actor_id':r.actor_id if r else None,'source':r.source if r else None}
    return json_value({'ticket_id':t.id,'transport':t.transport,'nodes':nodes,'actual_arrivals':[record(r) for r in db.scalars(select(Arrival).where(Arrival.ticket_id==t.id).order_by(Arrival.occurred_on,Arrival.id))],'physical_releases':[record(r)|{'photo_ids':list(db.scalars(select(PhysicalPhoto.attachment_id).where(PhysicalPhoto.release_id==r.id).order_by(PhysicalPhoto.id)))} for r in db.scalars(select(PhysicalRelease).where(PhysicalRelease.ticket_id==t.id).order_by(PhysicalRelease.occurred_at))]})


def phone(value):
    return value if isinstance(value,str) and re.fullmatch(r'\+[1-9]\d{7,14}',value) else None


def source_phone(obj):
    if isinstance(obj,Ticket):return phone(obj.phone_normalized) if obj.phone_confirmed else None
    # Native receipt stores a supplied recipient phone, without inference from party/mark.
    if isinstance(obj,Receipt):return phone(obj.customer_phone)
    return phone(obj.contact_raw)  # selected, committed external observation only


def proof(obj,last4):
    recipient=source_phone(obj)
    return bool(recipient and isinstance(last4,str) and re.fullmatch(r'\d{4}',last4) and recipient.endswith(last4))


def rate_limit(db,remote,kind,limit=30):
    """Caller returns an error response instead of raising so failed attempts commit."""
    bucket=hashlib.sha256((kind+':'+remote).encode()).hexdigest()
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int.from_bytes(bytes.fromhex(bucket)[:8],'big',signed=True)})
    now=utcnow();row=db.scalar(select(PublicAttempt).where(PublicAttempt.bucket==bucket).with_for_update())
    if not row:row=PublicAttempt(bucket=bucket,window_start=now,count=0);db.add(row)
    if now-row.window_start>timedelta(minutes=10):row.window_start=now;row.count=0
    row.count+=1;db.flush();return row.count<=limit


def public_query(db,reference,last4,language='en'):
    if not isinstance(reference,str) or not 1<=len(reference)<=200:return []
    rows=[];native=list(db.scalars(select(Receipt).where((Receipt.reference==reference)|(Receipt.courier_number==reference),Receipt.state=='active')))
    # Every candidate is proved individually BEFORE aggregation, including duplicate receipts.
    for r in native:
        if not proof(r,last4):continue
        rows.append({'source':'native_inbound','id':r.id,'receipt':r.reference,'transport':r.transport,'received_on':r.received_on,'pieces':r.pieces,'kg':r.kg,'cbm':r.cbm,'destination':r.destination})
    native_ids=[r.id for r in native]
    for t in db.scalars(select(Ticket).where((Ticket.receipt==reference)|(Ticket.native_receipt_id.in_(native_ids)),Ticket.state=='active')):
        if not proof(t,last4) or db.get(PackingDocument,t.document_id).state!='active':continue
        nodes=timeline(db,t.id)
        rows.append({'source':'packing_ticket','id':t.id,'receipt':t.receipt,'transport':t.transport,'destination':t.destination,'pieces':t.pieces,'kg':t.kg,'cbm':t.cbm,
            'nodes':[{'node':k,'label':v['labels'].get(language,v['labels']['en']),'occurred_at':v['occurred_at']} for k,v in nodes['nodes'].items()],
            'actual_arrivals':[{'occurred_on':r['occurred_on'],'pieces':r['pieces']} for r in nodes['actual_arrivals']],
            'actual_pickups':[{'occurred_at':r['occurred_at'],'pieces':r['pieces']} for r in nodes['physical_releases']]})
    for s in db.scalars(select(CargoSourceRow).join(ImportBatch,CargoSourceRow.batch_id==ImportBatch.id).where(CargoSourceRow.receipt==reference,CargoSourceRow.disposition=='selected',ImportBatch.kind=='f6',ImportBatch.status=='committed')):
        if proof(s,last4):rows.append({'source':'historical_external_observation','id':s.id,'receipt':s.receipt,'received_on':s.received_on,'pieces':s.pieces,'kg':s.kg,'cbm':s.cbm,'destination':s.destination,'native_stock_confirmed':False})
    return json_value(rows)


def internal_timeline(db,actor,ticket_id):
    t=locked(db,Ticket,ticket_id);require(actor,'tracking.read',t.warehouse_id)
    return timeline(db,ticket_id)
