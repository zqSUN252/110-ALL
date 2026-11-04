from sqlalchemy import select
from app.common import DomainError,require,check_version
from app.models import Shipment,Invoice
from app.finance import service as finance
from app.cargo.commands import command,object_access,enum,port,visible,member

FIELDS='reference transport origin destination clearing_on clearing_party_id clearing_amount_ngn'


def shipment_values(p):
    r={k:v for k,v in p.items() if k in FIELDS.split()}
    if 'transport' in r:enum(r['transport'],('SEA','AIR','EXPRESS','WAREHOUSE'),'transport')
    if 'origin' in r and r['origin']:enum(r['origin'],('GZ','HK'),'origin')
    if 'destination' in r:r['destination']=port(r['destination'])
    if 'clearing_on' in r:r['clearing_on']=finance.day(r['clearing_on']) if r['clearing_on'] else None
    if 'clearing_amount_ngn' in r:r['clearing_amount_ngn']=finance.decimal(r['clearing_amount_ngn']) if r['clearing_amount_ngn'] is not None else None
    return r


def clearing_sync(db,actor,s,p):
    from app.models import LegacyBill,LegacyShipment
    legacy=db.scalar(select(LegacyBill).join(LegacyShipment,LegacyShipment.id==LegacyBill.shipment_id).where(LegacyShipment.native_id==s.id,LegacyBill.kind=='AP'))
    if legacy:
        if {'clearing_party_id','clearing_amount_ngn','clearing_on'}.intersection(p):
            raise DomainError('legacy_payable_review','Historical payable requires explicit shared control reconciliation',409)
        return
    if s.clearing_amount_ngn is None:return
    if not s.clearing_party_id or not s.clearing_on:raise DomainError('clearing_basis','Clearing party and date required with a cost')
    member(db,actor,s.clearing_party_id,s.warehouse_id,'payables.manage')
    if s.clearing_invoice_id:
        inv=finance.locked(db,Invoice,s.clearing_invoice_id)
        if inv.party_id!=s.clearing_party_id:raise DomainError('clearing_party_locked','Existing payable keeps its original creditor; explicitly resolve financial relationship first',409)
        if inv.amount_ngn!=s.clearing_amount_ngn:
            finance.adjust_invoice(db,actor,dict(id=inv.id,expected_version=inv.version,amount_ngn=str(s.clearing_amount_ngn),reason='Shipment clearing cost correction',preview=p.get('finance_preview'),idempotency_key=p['idempotency_key']+':clearing'))
    else:
        inv=finance.create_invoice(db,actor,dict(warehouse_id=s.warehouse_id,party_id=s.clearing_party_id,kind='AP',reference=s.reference,uploaded_on=s.clearing_on.isoformat(),period=s.clearing_on.strftime('%Y-%m'),amount_ngn=str(s.clearing_amount_ngn),source_key='shipment-clearing:'+s.id,idempotency_key=p['idempotency_key']+':clearing'))
        s.clearing_invoice_id=inv['id']


@command('shipment.create','warehouse_id '+FIELDS,'shipments.manage',financial=True)
def create_shipment(db,actor,p):
    member(db,actor,None,p['warehouse_id'],'shipments.manage')
    s=Shipment(warehouse_id=p['warehouse_id'],**shipment_values(p));db.add(s);db.flush();clearing_sync(db,actor,s,p);db.flush();return finance.record(s)


@command('shipment.update','id expected_version finance_preview '+FIELDS,'shipments.manage',Shipment,financial=True)
def update_shipment(db,actor,p):
    s=object_access(db,actor,Shipment,p['id'],'shipments.manage');check_version(s,p.get('expected_version'))
    if 'transport' in p and p['transport']!=s.transport:
        from app.models import PackingDocument,DispatchBatch
        if db.scalar(select(PackingDocument.id).where(PackingDocument.shipment_id==s.id)) or db.scalar(select(DispatchBatch.id).where(DispatchBatch.shipment_id==s.id)):raise DomainError('shipment_transport','Transport cannot change after cargo is related',409)
    for k,v in shipment_values(p).items():setattr(s,k,v)
    clearing_sync(db,actor,s,p);s.version+=1;db.flush();return finance.record(s)


def get_shipment(db,actor,identity):return finance.record(object_access(db,actor,Shipment,identity,'shipments.read'))


def list_shipments(db,actor,limit=100,offset=0,transport=None,state=None,destination=None,warehouse_id=None):
    require(actor,'shipments.read')
    if not 1<=limit<=200 or offset<0:raise DomainError('pagination','Invalid pagination')
    q=visible(select(Shipment),Shipment,actor)
    for field,value in [(Shipment.transport,transport),(Shipment.state,state),(Shipment.warehouse_id,warehouse_id)]:
        if value:q=q.where(field==value)
    if destination:q=q.where(Shipment.destination.icontains(destination,autoescape=True))
    return [finance.record(s) for s in db.scalars(q.order_by(Shipment.created_at.desc(),Shipment.id).limit(limit).offset(offset))]
