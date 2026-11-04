from app.packing.quantities import known_sum,remaining as quantity_remaining,nonkg_sea
"""Manufacturer control, freight authority and physical pickup are separate facts."""
from decimal import Decimal
import re
from sqlalchemy import select
from app.common import DomainError,require,check_version,json_value
from app.models import (Ticket,Receipt,PackingDocument,Shipment,HoldFact,ReleaseOrder,ReleaseMember,PhysicalRelease,PhysicalPhoto,Invoice,Payment,Allocation,Discount,Party,PartyWarehouse,PartyPhone,BankClaim,EvidenceCase,PaymentRegistration,Arrival,Warehouse)
from app.finance import service as finance
from app.finance.access import payment_access
from app.cargo.commands import integer,fields
from .access import command,fingerprint,instant,own_upload


def hold_state(db,kind,identity):
    key=HoldFact.ticket_id if kind=='ticket' else HoldFact.receipt_id
    rows=list(db.scalars(select(HoldFact).where(key==identity).order_by(HoldFact.created_at)))
    active=any(h.state=='active' for h in rows)
    latest=rows[-1] if rows else None
    label='REMOVED ON CREDIT' if latest and latest.cancelled_by and active else 'ON HOLD' if active else 'REMOVED ON HOLD DATED ON' if latest else None
    return {'active':active,'label':label,'facts':[finance.record(h) for h in rows]}


def cargo_hold_state(db,obj):
    own=hold_state(db,'ticket' if isinstance(obj,Ticket) else 'receipt',obj.id)
    if isinstance(obj,Ticket) and obj.native_receipt_id:
        native=hold_state(db,'receipt',obj.native_receipt_id)
        return {'active':own['active'] or native['active'],'label':'ON HOLD' if own['active'] or native['active'] else own['label'] or native['label'],'facts':own['facts']+native['facts']}
    return own


def physical_totals(db,identity):
    rows=list(db.scalars(select(PhysicalRelease).where(PhysicalRelease.ticket_id==identity)))
    return {'pieces':sum(r.pieces for r in rows),'kg':known_sum(r.kg for r in rows),'cbm':sum((r.cbm for r in rows),Decimal(0))}


def _basis(db,t):
    """Private validation snapshot. Never return this to a warehouse/public caller."""
    from app.packing.allocation import components
    d=db.get(PackingDocument,t.document_id);s=db.get(Shipment,d.shipment_id)
    inv=db.get(Invoice,t.invoice_id) if t.invoice_id else None
    allocations=list(db.scalars(select(Allocation).where(Allocation.invoice_id==t.invoice_id).order_by(Allocation.id))) if inv else []
    payments=[db.get(Payment,pid) for pid in sorted({a.payment_id for a in allocations})]
    claims=list(db.scalars(select(BankClaim).where(BankClaim.payment_id.in_([p.id for p in payments])).order_by(BankClaim.id))) if payments else []
    result={'ticket':finance.record(t),'document':finance.record(d),'shipment':finance.record(s),
        'destination':finance.record(db.get(Warehouse,t.destination_warehouse_id)) if t.destination_warehouse_id else None,
        'holds':hold_state(db,'ticket',t.id),'components':components(db,t),
        'arrivals':[finance.record(a) for a in db.scalars(select(Arrival).where(Arrival.ticket_id==t.id).order_by(Arrival.id))],
        'invoice':finance.invoice_json(db,inv) if inv else None,
        'allocations':[finance.record(a) for a in allocations],'payments':[finance.record(p) for p in payments],
        'claims':[finance.record(c) for c in claims]}
    from app.tracking.service import warehouse_basis
    result['warehouse_confirmation']=warehouse_basis(db,t)
    if t.native_receipt_id:result['native']=finance.record(db.get(Receipt,t.native_receipt_id));result['native_hold']=hold_state(db,'receipt',t.native_receipt_id)
    if t.party_id:
        result['party']=finance.record(db.get(Party,t.party_id))
        result['memberships']=[finance.record(m) for m in db.scalars(select(PartyWarehouse).where(PartyWarehouse.party_id==t.party_id).order_by(PartyWarehouse.id))]
        result['phones']=[finance.record(m) for m in db.scalars(select(PartyPhone).where(PartyPhone.party_id==t.party_id).order_by(PartyPhone.id))]
    return result


def _eligible(db,t,mode):
    basis=_basis(db,t)
    if t.state!='active' or basis['document']['state']!='active':raise DomainError('inactive_cargo','Cargo is not active',409)
    if basis['holds']['active'] or basis.get('native_hold',{}).get('active'):raise DomainError('manufacturer_hold','Active manufacturer hold prevents freight release',409)
    if not t.destination_warehouse_id:raise DomainError('destination_required','Confirmed actual destination warehouse required',409)
    if not basis['arrivals']:raise DomainError('arrival_required','Actual destination arrival required',409)
    if not basis['warehouse_confirmation']['covered_arrivals']:raise DomainError('warehouse_required','Actual warehouse receipt confirmation is required',409)
    inv=basis['invoice']
    if not inv or inv['state']=='void':raise DomainError('invoice_required','Current receivable required',409)
    if inv.get('historical_bank_state')=='disputed':raise DomainError('payment_disputed','Historical bank evidence requires source reconciliation',409)
    if any(p['bank_state']=='disputed' for p in basis['payments']):raise DomainError('payment_disputed','Payment evidence needs review',409)
    if any(p['state']!='posted' for p in basis['payments'] if sum(Decimal(a['amount_ngn']) for a in basis['allocations'] if a['payment_id']==p['id'])>0):raise DomainError('payment_invalid','Payment allocation is no longer valid',409)
    if mode!='credit' and Decimal(inv['outstanding_ngn'])>0:raise DomainError('outstanding','Invoice remains unpaid',409)
    return basis


@command('release.issue','members mode promise_on guarantor reason','release.issue')
def issue_order(db,actor,p):
    if p['mode'] not in ('paid','credit','waiver'):raise DomainError('release_mode','Choose paid, credit or explicit waiver')
    members=p['members']
    if not isinstance(members,list) or not 1<=len(members)<=200 or len({m['ticket_id'] for m in members})!=len(members):raise DomainError('members','Select distinct current tickets')
    from app.packing.service import document_groups
    selected={m['ticket_id'] for m in members}
    for document_id in {finance.locked(db,Ticket,i).document_id for i in selected}:
        for group in document_groups(db,actor,document_id):
            if selected.intersection(group['ticket_ids']) and not set(group['ticket_ids']).issubset(selected):raise DomainError('release_group','Select every current member of the release group',409)
    if p['mode']=='credit':
        require(actor,'release.credit')
        if not p.get('promise_on') or not str(p.get('guarantor','')).strip():raise DomainError('credit_basis','Promise day and guarantor required')
    order=ReleaseOrder(mode=p['mode'],actor_id=actor.id,promise_on=finance.day(p['promise_on']) if p.get('promise_on') else None,guarantor=p.get('guarantor'),reason=p.get('reason'))
    db.add(order);db.flush()
    waived=set()
    for m in sorted(members,key=lambda m:m['ticket_id']):
        fields(m,'ticket_id expected_version')
        t=finance.locked(db,Ticket,m['ticket_id']);check_version(t,m.get('expected_version'))
        require(actor,'finance.read',t.warehouse_id)
        if p['mode']=='credit':require(actor,'release.credit',t.warehouse_id)
        if p['mode']=='waiver' and t.invoice_id not in waived:
            require(actor,'release.waiver',t.warehouse_id)
            inv=finance.get_invoice(db,actor,t.invoice_id);owed=Decimal(inv['outstanding_ngn'])
            if owed<=0 or owed>5000:raise DomainError('waiver_amount','Explicit release waiver must be greater than zero and at most NGN 5000')
            finance.add_discount(db,actor,dict(id=t.invoice_id,expected_version=inv['version'],amount_ngn=str(owed),waiver=True,reason=p.get('reason'),idempotency_key=p['idempotency_key']+':waiver:'+t.id))
            waived.add(t.invoice_id)
        basis=_eligible(db,t,p['mode'])
        db.add(ReleaseMember(order_id=order.id,ticket_id=t.id,destination_warehouse_id=t.destination_warehouse_id,source_fingerprint=fingerprint(basis),source_snapshot=basis))
    db.flush()
    return {'id':order.id,'order_id':order.id,'version':order.version,'mode':order.mode,'promise_on':order.promise_on,'guarantor':order.guarantor,'members':members,'actual_pickup':False}


def _current(db,order):
    members=list(db.scalars(select(ReleaseMember).where(ReleaseMember.order_id==order.id).order_by(ReleaseMember.ticket_id)))
    if order.status!='issued' or not members:return False,members
    for m in members:
        t=db.get(Ticket,m.ticket_id)
        try:basis=_eligible(db,t,order.mode)
        except DomainError:return False,members
        if fingerprint(basis)!=m.source_fingerprint:return False,members
    return True,members


def eligible_order(db,actor,identity):
    order=finance.locked(db,ReleaseOrder,identity);valid,members=_current(db,order)
    result=[]
    for m in members:
        t=db.get(Ticket,m.ticket_id)
        if '*' not in actor.capabilities and (m.destination_warehouse_id not in actor.warehouse_ids or t.destination_warehouse_id not in actor.warehouse_ids):continue
        require(actor,'release.read',m.destination_warehouse_id)
        require(actor,'release.read',t.destination_warehouse_id)
        from app.tracking.service import warehouse_arrivals
        actual=warehouse_arrivals(db,t)
        arrived={k:known_sum(getattr(a,k) for a in actual) for k in ('pieces','kg','cbm')}
        used=physical_totals(db,t.id)
        result.append({'ticket_id':t.id,'receipt':t.receipt,'destination_warehouse_id':t.destination_warehouse_id,
            'covered_kg':{'value':arrived['kg'],'known_subtotal':sum((a.kg for a in actual if a.kg is not None),Decimal(0)),'pending_count':sum(a.kg is None for a in actual)},
            'original_kg':t.kg,'remaining':{k:quantity_remaining(arrived[k],used[k]) for k in used}})
    if not result:raise DomainError('forbidden','No authorized destination members',403)
    return json_value({'id':order.id,'version':order.version,'eligible':valid,'members':result,'physical_releases':pickup_history(db,actor,order.id,[m['ticket_id'] for m in result])})


def pickup_history(db,actor,order_id,ticket_ids):
    """Destination-scoped historical facts remain readable after stock is exhausted."""
    query=select(PhysicalRelease).where(PhysicalRelease.order_id==order_id,PhysicalRelease.ticket_id.in_(ticket_ids)).order_by(PhysicalRelease.occurred_at,PhysicalRelease.id)
    if '*' not in actor.capabilities:query=query.where(PhysicalRelease.warehouse_id.in_(actor.warehouse_ids))
    releases=list(db.scalars(query))
    photos={r.id:[] for r in releases}
    if releases:
        for photo in db.scalars(select(PhysicalPhoto).where(PhysicalPhoto.release_id.in_(photos)).order_by(PhysicalPhoto.id)):photos[photo.release_id].append(photo.attachment_id)
    return [finance.record(r)|{'photo_ids':photos[r.id]} for r in releases]


def list_orders(db,actor,limit=50,offset=0):
    require(actor,'release.read')
    if not 1<=limit<=200 or offset<0:raise DomainError('pagination','Use page size 1..200')
    q=select(ReleaseOrder).join(ReleaseMember).distinct()
    if '*' not in actor.capabilities:q=q.where(ReleaseMember.destination_warehouse_id.in_(actor.warehouse_ids))
    return [eligible_order(db,actor,o.id) for o in db.scalars(q.order_by(ReleaseOrder.created_at.desc(),ReleaseOrder.id).limit(limit).offset(offset))]


def photo_bytes(db,settings,release_id,attachment_id,actor=None,customer=None):
    from app.models import Attachment,TicketAttachment
    from app.attachments import read_file
    r=finance.locked(db,PhysicalRelease,release_id)
    if not db.scalar(select(PhysicalPhoto.id).where(PhysicalPhoto.release_id==r.id,PhysicalPhoto.attachment_id==attachment_id)):raise DomainError('not_found','Pickup photo unavailable',404)
    if customer:
        from app.tracking.customer import require_owned
        related=list(db.scalars(select(Ticket).join(PhysicalRelease,PhysicalRelease.ticket_id==Ticket.id).join(PhysicalPhoto,PhysicalPhoto.release_id==PhysicalRelease.id).where(PhysicalPhoto.attachment_id==attachment_id)))
        related+=list(db.scalars(select(Ticket).join(TicketAttachment,TicketAttachment.ticket_id==Ticket.id).where(TicketAttachment.attachment_id==attachment_id)))
        require_owned(db,customer,related)
    else:
        require(actor,'release.read',r.warehouse_id)
        require(actor,'release.read',finance.locked(db,Ticket,r.ticket_id).destination_warehouse_id)
    a=finance.locked(db,Attachment,attachment_id);return read_file(settings,a),a.media_type


def pickup_photo_scope(db,actor,order_id,ticket_id,expected_version):
    order=finance.locked(db,ReleaseOrder,order_id)
    projection=eligible_order(db,actor,order.id)
    member=next((m for m in projection['members'] if m['ticket_id']==ticket_id),None)
    if not member:raise DomainError('release_member','Ticket is outside the authorized instruction',403)
    require(actor,'release.physical',member['destination_warehouse_id'])
    check_version(order,expected_version)
    if not projection['eligible']:raise DomainError('stale_release','Re-confirm current instruction sources',409)
    if member['remaining']['pieces'] is None or Decimal(member['remaining']['pieces'])<=0:raise DomainError('remaining','No arrived pieces remain for pickup',409)
    return order


def pickup_photo_owner(order_id,ticket_id,version):
    return f'{order_id}:{ticket_id}:{version}'


def validate_pickup_image(content,media_type):
    from PIL import Image
    from io import BytesIO
    formats={'image/png':'PNG','image/jpeg':'JPEG','image/webp':'WEBP'}
    if media_type not in formats:raise DomainError('photo_invalid','Pickup evidence must be PNG, JPEG or WebP')
    try:
        with Image.open(BytesIO(content)) as im:
            if im.format!=formats[media_type]:raise ValueError()
            im.verify()
    except (OSError,ValueError,Image.DecompressionBombError):raise DomainError('photo_invalid','Pickup evidence must contain valid image bytes') from None


def uploaded_pickup_photo(db,settings,actor,order_id,ticket_id,version,attachment_id):
    from app.models import Attachment
    from app.attachments import read_file
    pickup_photo_scope(db,actor,order_id,ticket_id,version)
    photo=finance.locked(db,Attachment,attachment_id)
    if photo.owner_type!='release_photo' or photo.owner_id!=pickup_photo_owner(order_id,ticket_id,version) or photo.created_by!=actor.id:
        raise DomainError('forbidden','Photo is outside this pickup upload',403)
    return read_file(settings,photo),photo.media_type


@command('release.physical','order_id expected_version ticket_id pieces kg cbm occurred_at photo_ids','release.physical')
def physical_release(db,actor,p):
    order=finance.locked(db,ReleaseOrder,p['order_id']);check_version(order,p.get('expected_version'))
    valid,members=_current(db,order)
    if not valid:raise DomainError('stale_release','Release instruction is no longer valid; re-confirm current sources',409)
    t=finance.locked(db,Ticket,p['ticket_id'])
    if not any(m.ticket_id==t.id for m in members):raise DomainError('release_member','Ticket is outside this instruction',403)
    projection=eligible_order(db,actor,order.id)
    remaining=next(m['remaining'] for m in projection['members'] if m['ticket_id']==t.id)
    if p.get('kg') is None and not nonkg_sea(db,t):raise DomainError('quantities_unresolved','Known/KG-priced quantities require KG',409)
    quantities={'pieces':integer(p['pieces'],True),'kg':finance.decimal(p['kg'],6) if p.get('kg') is not None else None,'cbm':finance.decimal(p['cbm'],6)}
    if any(quantities[k] is None or Decimal(quantities[k])>Decimal(remaining[k]) for k in quantities if remaining[k] is not None):raise DomainError('remaining','Pickup exceeds remaining arrived quantities',409)
    if quantities['pieces']==int(remaining['pieces']) and any(quantities[k]!=Decimal(remaining[k]) for k in ('kg','cbm') if remaining[k] is not None):raise DomainError('remaining','Final pieces must take exact remaining KG and CBM',409)
    if not isinstance(p['photo_ids'],list) or not 1<=len(p['photo_ids'])<=20:raise DomainError('photo_required','Actual pickup photos required')
    from app.attachments import read_file
    from PIL import Image
    from io import BytesIO
    for pid in p['photo_ids']:
        from app.models import Attachment
        photo=finance.locked(db,Attachment,pid)
        if photo.owner_type=='release_photo':
            if photo.owner_id!=pickup_photo_owner(order.id,t.id,order.version) or photo.created_by!=actor.id:raise DomainError('forbidden','Photo belongs to another pickup or instruction version',403)
        else:photo=own_upload(db,actor,pid)
        try:
            with Image.open(BytesIO(read_file(db.info['settings'],photo))) as im:im.verify()
        except (OSError,ValueError):raise DomainError('photo_invalid','Pickup evidence must be a real image') from None
    row=PhysicalRelease(order_id=order.id,ticket_id=t.id,warehouse_id=t.destination_warehouse_id,actor_id=actor.id,occurred_at=instant(p['occurred_at']),**quantities)
    db.add(row);db.flush()
    for pid in set(p['photo_ids']):db.add(PhysicalPhoto(release_id=row.id,attachment_id=pid))
    order.version+=1
    return finance.record(row)|{'order_version':order.version}


@command('hold.remove','members reason removed_on confirmed_phone ngn_per_usd','release.hold')
def remove_holds(db,actor,p):
    if not isinstance(p['members'],list) or not 1<=len(p['members'])<=200 or len({m['hold_id'] for m in p['members']})!=len(p['members']):raise DomainError('hold_members','Choose 1..200 distinct hold facts')
    if not str(p.get('reason','')).strip() or not re.fullmatch(r'\+[1-9]\d{7,14}',p.get('confirmed_phone','')):raise DomainError('hold_basis','Removal reason and confirmed international phone required')
    rate=finance.decimal(p['ngn_per_usd'],6,True);on=finance.day(p['removed_on']);rows=[];shipments=set()
    for member in p['members']:
        fields(member,'hold_id expected_version source_version finance_preview')
        h=finance.locked(db,HoldFact,member['hold_id']);check_version(h,member.get('expected_version'))
        obj=finance.locked(db,Ticket if h.ticket_id else Receipt,h.ticket_id or h.receipt_id);check_version(obj,member.get('source_version'))
        if h.state!='active':raise DomainError('hold_state','Hold is not active',409)
        if h.ticket_id:shipments.add(db.get(PackingDocument,obj.document_id).shipment_id)
        rows.append((h,obj,member))
    if len(shipments)>1:raise DomainError('hold_container','Combined hold removal must be in the same actual shipment')
    for h,obj,member in rows:
        h.state='removed';h.removed_on=on;h.reason=p['reason'];h.confirmed_phone=p['confirmed_phone'];h.removal_ngn_per_usd=rate;h.removed_by=actor.id;h.cancelled_by=None;h.version+=1
        db.info.setdefault('hold_removing',set()).add(obj.id)
        try:
            if h.ticket_id:
                from app.packing.service import update_ticket
                update_ticket(db,actor,dict(id=obj.id,expected_version=obj.version,contact_raw=p['confirmed_phone'],phone_normalized=p['confirmed_phone'],phone_confirmed=True,ngn_per_usd=str(rate),finance_preview=member.get('finance_preview'),idempotency_key=p['idempotency_key']+':phone:'+obj.id))
            else:
                from app.cargo.service import update_receipt
                update_receipt(db,actor,dict(id=obj.id,expected_version=obj.version,customer_phone=p['confirmed_phone'],idempotency_key=p['idempotency_key']+':phone:'+obj.id))
        finally:db.info['hold_removing'].discard(obj.id)
    return {'id':rows[0][0].id,'members':[{'hold_id':h.id,'ticket_id':h.ticket_id,'receipt_id':h.receipt_id,'version':h.version,'removal_ngn_per_usd':h.removal_ngn_per_usd,'rate_applied_to_invoice':bool(h.ticket_id and obj.invoice_id)} for h,obj,m in rows],'next_action':'arrival','label':'REMOVED ON HOLD DATED ON','removed_on':on}


@command('hold.cancel','id expected_version reason','release.supervise','hold_id')
def cancel_removal(db,actor,p):
    h=finance.locked(db,HoldFact,p['id']);check_version(h,p.get('expected_version'))
    if h.state!='removed' or not str(p.get('reason','')).strip():raise DomainError('hold_state','Only an existing removal can be cancelled with a reason',409)
    h.state='active';h.cancelled_by=actor.id;h.cancellation_reason=p['reason'];h.version+=1
    return finance.record(h)|{'label':'REMOVED ON CREDIT','credit_authorized':False}


@command('payment.register','invoice_id expected_version payment_id case_id source_version source','release.register')
def register_payment(db,actor,p):
    inv=finance.locked(db,Invoice,p['invoice_id']);require(actor,'release.register',inv.warehouse_id);require(actor,'finance.read',inv.warehouse_id);check_version(inv,p.get('expected_version'))
    if bool(p.get('payment_id'))==bool(p.get('case_id')):raise DomainError('registration_source','Choose one actual payment or evidence source')
    if not str(p.get('source','')).strip():raise DomainError('registration_source','Source explanation required')
    if p.get('payment_id'):
        src=finance.locked(db,Payment,p['payment_id']);payment_access(db,actor,src,'finance.read')
        if src.party_id!=inv.party_id or src.scope!=inv.scope:raise DomainError('registration_party','Payment and invoice ownership must agree')
    else:
        src=finance.locked(db,EvidenceCase,p['case_id']);require(actor,'evidence.read',src.warehouse_id)
        if src.warehouse_id!=inv.warehouse_id or src.scope!=inv.scope:raise DomainError('registration_scope','Evidence and invoice scope must agree')
    check_version(src,p.get('source_version'))
    row=PaymentRegistration(invoice_id=inv.id,payment_id=p.get('payment_id'),case_id=p.get('case_id'),source=p['source'],actor_id=actor.id);db.add(row);db.flush()
    return finance.record(row)


def receivable_state(db,actor,ticket_id):
    t=finance.locked(db,Ticket,ticket_id);require(actor,'release.read',t.warehouse_id)
    inv=finance.get_invoice(db,actor,t.invoice_id) if t.invoice_id else None
    registrations=list(db.scalars(select(PaymentRegistration).where(PaymentRegistration.invoice_id==t.invoice_id,PaymentRegistration.active.is_(True)))) if inv else []
    valid=[]
    for o in db.scalars(select(ReleaseOrder).join(ReleaseMember).where(ReleaseMember.ticket_id==t.id)):
        if _current(db,o)[0]:valid.append(o)
    allocated=Decimal(inv['allocated_ngn']) if inv else Decimal(0);owed=Decimal(inv['outstanding_ngn']) if inv else None
    registered=bool(registrations) or allocated>0
    credit=any(o.mode=='credit' for o in valid)
    color='yellow' if credit or (allocated>0 and owed and owed>0 and valid) else 'red' if not registered else None
    return json_value({'ticket_id':t.id,'invoice':inv,'registered':registered,'registration_ids':[r.id for r in registrations],'valid_order_ids':[o.id for o in valid],'credit':credit,'color':color,'physical':physical_totals(db,t.id)})
