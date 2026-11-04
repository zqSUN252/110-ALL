from decimal import Decimal
from sqlalchemy import select
from app.common import DomainError,require,check_version
from app.models import Ticket,Receipt,Notification,NotificationMember,CopyEvent,ReleaseOrder,ReleaseMember,Arrival,HoldFact,Payment,EvidenceCase,AppSetting,Invoice,utcnow
from app.finance import service as finance
from .access import command,authorize,fingerprint
from .projection import packing_projection,today,shown,total_text,rates,profile
from . import release

VERSIONS={'arrival':'ARRIVAL-510-512-v1','receipt':'RECEIPT-527-v1','release':'RELEASE-486-541-543-v1','hold':'HOLD-345-v1','remove_hold':'REMOVE-HOLD-464-465-560-v1','loading':'LOADING-552-v1','rates':'RATE-185-207-v1'}
COMPANY='GUANGZHOU MC STAR LOGISTICS CO.,LTD'


def credit_debt(db,actor,rows):
    ids={r['invoice_id'] for r in rows if r['invoice_id']};parties={r['party_id'] for r in rows if r['party_id']};scopes={db.get(Invoice,i).scope for i in ids};old=[]
    for inv in db.scalars(select(Invoice).where(Invoice.kind=='AR',Invoice.state!='void',Invoice.party_id.in_(parties),Invoice.scope.in_(scopes),Invoice.id.not_in(ids)).order_by(Invoice.id)):
        current=finance.get_invoice(db,actor,inv.id)
        old.append({'invoice_id':inv.id,'version':inv.version,'outstanding_ngn':current['outstanding_ngn']})
    return {'invoices':old,'total_ngn':str(sum((max(Decimal(0),Decimal(r['outstanding_ngn'])) for r in old),Decimal(0)))}


def _charge_lines(r):
    # A notice's payable total belongs to the existing invoice. For partial
    # arrivals its billed currency/components differ from the full-ticket quote.
    if r.get('invoice_id') and 'arrival_view' in r:r=dict(r,**r['arrival_view'])
    canonical=all((c['component'],c['currency'],c['unit']) in (('freight','USD','CBM'),('clearing','NGN','CBM')) for c in r['charges'])
    if r['transport']=='SEA' and canonical:
        usd=r['freight_usd'];fx=r['ngn_per_usd']
        converted=finance.settle(usd,'USD',{'direction':'NGN_PER_USD','rate':fx}) if usd is not None and fx is not None else None
        lines=[f"Freight: US${shown(usd)} ×{shown(fx)} = ₦{shown(converted)}",f"Clearance Fee: ₦{shown(r['clearing_ngn'])}"]
    else:
        lines=[f"{c['currency']} {shown(c['unit_price'])} PER {c['unit']} × {shown(c['quantity'])} = {c['currency']} {shown(c['amount'])}" for c in r['charges'] if c['amount'] is None or Decimal(c['amount'])!=0]
        if not lines:lines=['Charging basis: TO BE CONFIRMED']
    if r['rounding_adjustment_ngn'] is not None and Decimal(r['rounding_adjustment_ngn'])!=0:lines.append(f"Rounding adjustment (NGN): {r['rounding_adjustment_ngn']}")
    return '\n'.join(lines)


def text_body(db,kind,data,order=None):
    rows=[dict(r,**r.get('arrival_view',{})) if kind in ('arrival','hold') else r for r in data['rows']];cfg=data['profile'];date=data['generated_on'];out=[]
    for index,r in enumerate(rows):
        receipts=shown(r['receipt']);mark=shown(r['mark']);contact=shown(r['phone']);warehouse=shown(r['warehouse']);container=shown(r['container']);goods=' / '.join(v for v in (r['item_zh'],r['item_en']) if v) or 'TO BE CONFIRMED'
        if kind=='receipt':
            price=data['rate_dependencies'][index];dest=shown(r['destination'])
            arrangement=f"We will arrange the shipment to {dest}\nand keep you updated on the progress." if r['destination'] else 'Destination: TO BE CONFIRMED. Shipment destination awaits your confirmation.'
            out.append(f'''Dear “{mark}”,
Your {r['transport']} cargo (**Receipt No. {receipts}**,
**Tracking code {receipts}**) has been safely received
at our **{shown(r['origin_warehouse'])}**.

📦 **Shipment Details**:
- **Goods**: {goods}; ({shown(r['pieces'])} PCS)
- **Weight**: {shown(r['kg'])} KG
- **CBM**: {shown(r['cbm'])}cbm
- **Destination**: {dest}
- **Received Date**: {shown(r['received_on'])}

{arrangement}
Should you have any questions, please feel free to contact us:
**{shown(cfg.get('china_contacts') or r['origin_contact'])}**

Best regards,
**MC STAR LOGISTICS**
{date}
------------------------------------------------------------
💰 **CHARGING STANDARD — {price['heading']}** (effective dates per card)
{chr(10).join(price['lines'])}
Subject to Nigeria Customs: if additional taxes are imposed,
clearing fee charged at actual cost.
Sensitive items & chemicals quoted separately.''')
        elif kind=='arrival':
            out.append(f'''*📢 GOODS ARRIVAL PICKUP & PAYMENT NOTICE*
{warehouse} warehouse
👤 Dear {mark} ({contact}),
📦 Consignment for {mark} is READY FOR PICK UP
at our *{warehouse}* warehouse.

━━━━ ① PACKING LIST INFO ━━━━
Receipt No.: {receipts}
Container No.: {container}
Commodity: {goods}
Packages: {shown(r['pieces'])} PCS
Volume: {shown(r['cbm'])} CBM

━━━━ ② BILLs ━━━━
{_charge_lines(r)}
--------------------------------------------------------------------------
💰▶️ Total: *₦{shown(r['invoice_total_ngn'])}*
🏢 Warehouse Address: {shown(r['address'])}
Contacts: {shown(r['destination_contact'])}
🏦 Bank Info
**{shown(cfg.get('bank_name'))}** | **{shown(cfg.get('account_holder'))}** | **{shown(cfg.get('account_number'))}**

Thanks for your support
*{COMPANY}*
*{date}*''')
        elif kind=='loading':
            from app.tracking.service import timeline
            node=timeline(db,r['id'])['nodes']['loading'];loaded=node['occurred_at']
            if not loaded or not r['container']:raise DomainError('loading_missing','Actual loading date and actual container/waybill are required',409)
            from datetime import datetime
            from zoneinfo import ZoneInfo
            loaded_day=datetime.fromisoformat(loaded).astimezone(ZoneInfo(db.info['settings'].business_timezone)).date().isoformat()
            out.append(f'''⭐️ Dear friend,
Your goods have been loaded with MC STAR.
Please check the attached Packing List.

🚚 Loading date: *{loaded_day}*
Container No.: *{container}*

Thanks for your support.
*MC STAR China Office*
📞 TEL: *{shown(cfg.get('packing_list_contact'))}* (for Packing List requests)
📞 TEL: *{shown(cfg.get('customer_care_contact'))}* (Customer Care)
🌐 Self-service tracking website: *{shown(cfg.get('tracking_domain'))}*''')
        elif kind=='rates':
            price=data['rate_dependencies'][index];out.append(f"💰 CHARGING STANDARD — {price['heading']}\n"+'\n'.join(price['lines']))
    if kind in ('hold','remove_hold','release'):
        groups={}
        for r in rows:groups.setdefault((r['destination_warehouse_id'],r['container']),[]).append(r)
        for (_,container),members in groups.items():
            first=members[0];warehouse=shown(first['warehouse']);details='\n'.join(f"📦 {shown(r['receipt'])} / {shown(r['mark'])} / {shown(r['pieces'])} PCS / {shown(r['cbm'])} CBM\n（CONTAINER NO.: {shown(container)}）" for r in members)
            from .projection import aggregate
            pcs=total_text(aggregate(members,'pieces'));cbm=total_text(aggregate(members,'cbm'));total=total_text(aggregate(members,'invoice_total_ngn'))
            if kind=='hold':out.append(f"{details}\n\n您好，{len(members)} 票控货的货物已完成清关入我方 {warehouse} 仓库，请通知客户尽快结清货款。\n如客户已结清货款，请尽快告知收货人姓名及联系电话，谢谢。")
            elif kind=='remove_hold':
                holds=[h for r in members for h in release.cargo_hold_state(db,db.get(Ticket if r['source_type']=='ticket' else Receipt,r['id']))['facts'] if h['state']=='removed']
                out.append(f'''📦 REMOVE ON HOLD INSTRUCTION - {warehouse} WAREHOUSE
Container: {shown(container)}
Total: {len(members)} Receipts / {pcs} Packages / {cbm} CBM
Mark: {shown(first['mark'])}

--- RECEIPTS（本次合并放货）---
{details}
--- END OF RECEIPTS ---

{chr(10).join(_charge_lines(r) for r in members)}
Total Payable: NGN {total}
Client Contact: {shown(first['phone'])}
{chr(10).join('Reason: '+shown(h['reason'])+chr(10)+'Released On: '+shown(h['removed_on'])+chr(10)+'Approved By: '+shown(h['removed_by']) for h in holds)}

✅ The ON HOLD on the above {len(members)} receipts has been removed ON HOLD by Supplier,
pls notify Customer to pay our Freight and carry goods ontime to avoid the demurrage and damage of the goods in warehouse.

<Thanks for your supporting>
{COMPANY}
{date}''')
            else:
                payments=[]
                for r in members:
                    basis=release._basis(db,db.get(Ticket,r['id']))
                    for p in basis['payments']:
                        allocated=sum((Decimal(a['amount_ngn']) for a in basis['allocations'] if a['payment_id']==p['id']),Decimal(0))
                        if allocated<=0:continue
                        evidence=db.scalar(select(EvidenceCase).where(EvidenceCase.payment_id==p['id']).order_by(EvidenceCase.created_at.desc()))
                        payments.append(f"{shown(evidence.bank_name if evidence else p['approval'])} | {shown(evidence.payer_name if evidence else None)} | **NGN {allocated} | {shown(p['paid_on'])}**")
                        original=finance.payment_json(db,db.get(Payment,p['id']))
                        if Decimal(original['available_ngn'])>0:payments.append(f"Original received: NGN {p['amount_ngn']} | Unallocated carry-forward balance: NGN {original['available_ngn']}")
                received=total_text(aggregate(members,'allocated_ngn'));owed=total_text(aggregate(members,'outstanding_ngn'))
                conclusion='RELEASE ON CREDIT, bill cares by client' if order.mode=='credit' else 'Balance waived to release' if order.mode=='waiver' else 'FULL RELEASE - release all goods'
                if order.mode=='credit':conclusion+=f"\nPromise payment day: {order.promise_on}\nGuarantor: {order.guarantor}\nOther historical debt: NGN {data['credit_debt']['total_ngn']}"
                if order.mode=='waiver':conclusion+='\nExplicit discount (NGN): '+total_text(aggregate(members,'discount_ngn'))
                out.append(f'''📦 **RELEASE INSTRUCTIONS - {warehouse} WAREHOUSE**
Client Mark: **{shown(first['mark'])} Tel: {shown(first['phone'])}**
====================================================
SHIPMENT DETAILS
{details}
Total Volume: {cbm} CBM
Total Packages: **{pcs} PCS**
Full Original Invoice Payable Amount: NGN {total}

PAYMENT SUMMARY
Received Payment List:
{chr(10).join(payments) or 'No allocated payment'}
Total Received: NGN {received}
Outstanding Balance: NGN {owed}
{conclusion}

<Thanks for your support>
Issued by: **{COMPANY}**
Date: **{date}**''')
    return '\n\n'.join(out)


@command('notification.render','kind entry ticket_ids receipt_ids order_id','documents.notify')
def render(db,actor,p):
    kind=p['kind']
    if kind not in VERSIONS:raise DomainError('template','Unknown notification template')
    if p.get('entry')=='packing-row' and kind!='loading':raise DomainError('entry','Packing-row action is the loading notice')
    if kind=='loading' and p.get('entry')!='packing-row':raise DomainError('entry','Loading notice uses the packing-row entrance')
    objects=[finance.locked(db,Ticket,i) for i in p.get('ticket_ids',[])]+[finance.locked(db,Receipt,i) for i in p.get('receipt_ids',[])]
    if kind in ('receipt','arrival','hold','loading'):
        from app.packing.service import document_groups
        selected={o.id for o in objects if isinstance(o,Ticket)}
        for document_id in {o.document_id for o in objects if isinstance(o,Ticket)}:
            for group in document_groups(db,actor,document_id):
                if selected.intersection(group['ticket_ids']):selected.update(group['ticket_ids'])
        objects=[o for o in objects if isinstance(o,Receipt)]+[finance.locked(db,Ticket,i) for i in sorted(selected)]
        authorize(db,actor,{'ticket_ids':list(selected)},'documents.notify')
    if not 1<=len(objects)<=200 or len({(type(o),o.id) for o in objects})!=len(objects):raise DomainError('members','Select 1..200 distinct source rows')
    order=None
    if kind=='release':
        order=finance.locked(db,ReleaseOrder,p['order_id']);valid,ms=release._current(db,order)
        if not valid or {o.id for o in objects}!={m.ticket_id for m in ms}:raise DomainError('stale_release','All current instruction members must be valid and selected',409)
        from app.finance.access import payment_access
        for member in ms:
            for pay in member.source_snapshot.get('payments',[]):payment_access(db,actor,finance.locked(db,Payment,pay['id']),'finance.read')
    for obj in objects:
        if obj.state!='active':raise DomainError('inactive_cargo','Source is not active',409)
        if isinstance(obj,Ticket) and obj.invoice_id:finance.get_invoice(db,actor,obj.invoice_id)
        held=release.cargo_hold_state(db,obj)
        if kind in ('arrival','hold'):
            if not isinstance(obj,Ticket) or not db.scalar(select(Arrival.id).where(Arrival.ticket_id==obj.id)) or not obj.destination_warehouse_id:raise DomainError('arrival_required','Actual destination arrival and warehouse required',409)
            from app.tracking.service import warehouse_arrivals,effective_node
            if not warehouse_arrivals(db,obj):raise DomainError('warehouse_required','Actual applicable warehouse receipt confirmation is required',409)
            customs=effective_node(db,obj,'customs_cleared')
            if kind=='hold' and (not customs or not customs.occurred_at):raise DomainError('customs_required','Actual customs clearance confirmation is required for the hold arrival notice',409)
            if kind=='arrival' and held['active']:raise DomainError('manufacturer_hold','Use the manufacturer hold notice',409)
            if kind=='hold' and not held['active']:raise DomainError('hold_required','Only current manufacturer holds use this notice')
        if kind=='remove_hold' and (held['active'] or not any(h['state']=='removed' for h in held['facts'])):raise DomainError('hold_state','No valid current removal; cancellation cannot assert hold removed',409)
        if kind=='receipt' and obj.received_on is None:raise DomainError('receipt_date','Actual received date required',409)
    data=packing_projection(db,objects)
    if kind in ('rates','receipt'):data['rate_dependencies']=[rates(db,[r]) for r in data['rows']]
    if order and order.mode=='credit':data['credit_debt']=credit_debt(db,actor,data['rows'])
    body=text_body(db,kind,data,order)
    n=Notification(kind=kind,template_version=VERSIONS[kind],actor_id=actor.id,generated_on=today(db),timezone=db.info['settings'].business_timezone,body=body,source_snapshot=data,order_id=order.id if order else None)
    db.add(n);db.flush()
    for obj in objects:db.add(NotificationMember(notification_id=n.id,ticket_id=obj.id if isinstance(obj,Ticket) else None,receipt_id=obj.id if isinstance(obj,Receipt) else None,source_version=obj.version,warehouse_id=obj.warehouse_id))
    return {'id':n.id,'notification_id':n.id,'version':n.version,'template_version':n.template_version,'text':body,'generated_on':n.generated_on,'ticket_ids':[o.id for o in objects if isinstance(o,Ticket)],'receipt_ids':[o.id for o in objects if isinstance(o,Receipt)],'delivery_status':'not_sent','copy_action':'clipboard_text_or_png'}


def current_generation(db,actor,n):
    authorize(db,actor,{'notification_id':n.id},'documents.notify')
    objects=[]
    for m in db.scalars(select(NotificationMember).where(NotificationMember.notification_id==n.id)):
        require(actor,'documents.notify',m.warehouse_id)
        obj=finance.locked(db,Ticket if m.ticket_id else Receipt,m.ticket_id or m.receipt_id)
        if isinstance(obj,Ticket) and obj.invoice_id:finance.get_invoice(db,actor,obj.invoice_id)
        hold=release.cargo_hold_state(db,obj)
        if n.kind in ('arrival','remove_hold') and hold['active']:raise DomainError('manufacturer_hold','Current manufacturer hold requires a fresh hold notice',409)
        if n.kind=='hold' and not hold['active']:raise DomainError('stale_generation','Hold state changed; generate fresh content',409)
        objects.append(obj)
    # Rebuild in the generation's original order and compare all displayed facts.
    indexed={o.id:o for o in objects};objects=[indexed[r['id']] for r in n.source_snapshot['rows']]
    current=packing_projection(db,objects);current['generated_on']=n.source_snapshot['generated_on']
    if n.kind in ('rates','receipt'):current['rate_dependencies']=[rates(db,[r]) for r in current['rows']]
    if 'credit_debt' in n.source_snapshot:current['credit_debt']=credit_debt(db,actor,current['rows'])
    if fingerprint(current)!=fingerprint(n.source_snapshot):raise DomainError('stale_generation','Current source changed; generate fresh content',409)
    if n.order_id and not release._current(db,db.get(ReleaseOrder,n.order_id))[0]:raise DomainError('stale_release','Instruction no longer current',409)
    return objects


@command('notification.copy','id expected_version clipboard_success','documents.notify','notification_id')
def copy_success(db,actor,p):
    n=finance.locked(db,Notification,p['id']);check_version(n,p.get('expected_version'))
    if p.get('clipboard_success') is not True:raise DomainError('clipboard_failed','Clipboard failure cannot mark copied')
    if n.generated_on!=today(db):raise DomainError('stale_generation','Generate fresh content at copy time; signature date changed',409)
    current_generation(db,actor,n)
    members=list(db.scalars(select(NotificationMember).where(NotificationMember.notification_id==n.id)))
    for m in members:
        obj=finance.locked(db,Ticket if m.ticket_id else Receipt,m.ticket_id or m.receipt_id)
        if obj.version!=m.source_version:raise DomainError('stale_generation','Source changed; regenerate before copying',409)
    if n.order_id and not release._current(db,db.get(ReleaseOrder,n.order_id))[0]:raise DomainError('stale_release','Instruction no longer current',409)
    c=CopyEvent(notification_id=n.id,actor_id=actor.id);db.add(c);db.flush()
    return {'id':c.id,'version':c.version,'notification_id':n.id,'ticket_ids':[m.ticket_id for m in members if m.ticket_id],'receipt_ids':[m.receipt_id for m in members if m.receipt_id],'copied':True,'delivered':False}


@command('notification.cancel','id expected_version reason','documents.notify','copy_id')
def cancel_copy(db,actor,p):
    c=finance.locked(db,CopyEvent,p['id']);check_version(c,p.get('expected_version'))
    if not str(p.get('reason','')).strip():raise DomainError('reason','Cancellation reason required')
    c.cancelled_by=actor.id;c.cancelled_at=utcnow();c.reason=p['reason'];c.version+=1
    members=list(db.scalars(select(NotificationMember).where(NotificationMember.notification_id==c.notification_id)))
    return {'id':c.id,'version':c.version,'notification_id':c.notification_id,'ticket_ids':[m.ticket_id for m in members if m.ticket_id],'receipt_ids':[m.receipt_id for m in members if m.receipt_id],'cancelled':True}


def marks(db,source_ids,source_type='ticket',include_cancelled=False):
    column={'ticket':NotificationMember.ticket_id,'receipt':NotificationMember.receipt_id}[source_type]
    result={i:[] for i in source_ids}
    query=select(NotificationMember,Notification,CopyEvent).join(Notification,Notification.id==NotificationMember.notification_id).join(CopyEvent,CopyEvent.notification_id==Notification.id).where(column.in_(source_ids))
    if not include_cancelled:query=query.where(CopyEvent.cancelled_at.is_(None))
    for m,n,c in db.execute(query.order_by(CopyEvent.created_at,CopyEvent.id)):
        result[getattr(m,column.key)].append({'copy_id':c.id,'version':c.version,'kind':n.kind,'copied_at':c.created_at.isoformat(),'actor_id':c.actor_id,'label':'📦已通知装柜 ✕' if n.kind=='loading' else '已复制通知','background':'yellow' if n.kind=='loading' else None,'foreground':'black' if n.kind=='loading' else None})
        if include_cancelled:result[getattr(m,column.key)][-1].update(cancelled_at=c.cancelled_at.isoformat() if c.cancelled_at else None,cancelled_by=c.cancelled_by,reason=c.reason)
    return result
