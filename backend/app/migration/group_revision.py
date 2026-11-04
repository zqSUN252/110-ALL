"""Whole-group changes preserve source controls, applications and irreversible facts."""
from types import SimpleNamespace
from decimal import Decimal
from sqlalchemy import select,text
from app.common import DomainError,require,check_version,run_command,audit,json_value
from app.models import (LegacyBill,LegacyBillMember,LegacyTicket,LegacyBillApplication,LegacyGroupRevision,
    Ticket,Invoice,Payment,Allocation,Discount,Arrival,PhysicalRelease,PackingDocument,Shipment,Party,
    LegacyBankCoverage,LegacyBankEvidence,BankAccount)
from app.finance import service as finance
from app.finance.access import party_member,payment_access,account_access
from app.packing import service as packing,pricing,allocation
from app.packing.quantities import known_sum,nonkg_sea
from app.cargo.commands import native_change,fields
from app.migration.service import legacy_access
from app.migration.source import digest
from app.migration.operations import fact_snapshot


def current(db,actor,identity):
    require(actor,'legacy.resolve');legacy_access(actor,True)
    bill=finance.locked(db,LegacyBill,identity)
    if not bill.native_id or bill.relation_state not in ('declared_membership','confirmed_membership','declared_shipment'):
        raise DomainError('legacy_group_review','Complete source membership and native ownership must be resolved first',409)
    inv=finance.locked(db,Invoice,bill.native_id)
    for cap in ('finance.read','finance.adjust','packing.manage','packing.read'):require(actor,cap,inv.warehouse_id)
    from app.finance.authorization import authorize
    authorize(db,actor,'invoice.adjust',{'id':inv.id,'discount_total_ngn':'0'})
    if inv.kind=='AP':require(actor,'payables.manage',inv.warehouse_id)
    app=db.scalar(select(LegacyBillApplication).where(LegacyBillApplication.bill_id==bill.id).with_for_update())
    if not app:raise DomainError('legacy_control_missing','Current source application missing',409)
    sources=list(db.scalars(select(LegacyTicket).join(LegacyBillMember,LegacyBillMember.ticket_id==LegacyTicket.id).where(LegacyBillMember.bill_id==bill.id).order_by(LegacyTicket.id)))
    if inv.kind=='AP':sources=[]  # Clearing cost is separate from customers' ticket AR.
    if any(not t.native_id for t in sources):raise DomainError('legacy_group_review','Resolve every known source member before group revision',409)
    tickets=[finance.locked(db,Ticket,t.native_id) for t in sources]
    for t in tickets:
        if inv.kind=='AR' and t.invoice_id!=inv.id:raise DomainError('group_invoice_conflict','Every member must retain the same original invoice',409)
        for wh in {t.warehouse_id,t.destination_warehouse_id}-{None}:
            for cap in ('packing.manage','finance.read'):require(actor,cap,wh)
        packing.financial_access(db,actor,t)
    payments=list(db.scalars(select(Payment).join(Allocation,Allocation.payment_id==Payment.id).where(Allocation.invoice_id==inv.id).distinct().order_by(Payment.id)))
    for pay in payments:payment_access(db,actor,pay,'finance.adjust')
    proofs=list(db.scalars(select(LegacyBankEvidence).join(LegacyBankCoverage,LegacyBankCoverage.evidence_id==LegacyBankEvidence.id).where(LegacyBankCoverage.bill_id==bill.id)))
    from app.migration.bank_evidence import read_access
    for proof in proofs:read_access(db,actor,proof)
    return bill,inv,app,tickets,payments,proofs


def preview(db,actor,identity,p):
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    bill,inv,app,tickets,payments,proofs=current(db,actor,identity)
    for obj,key in ((bill,'expected_version'),(inv,'invoice_version'),(app,'application_version')):check_version(obj,p.get(key))
    action=p.get('action')
    if action not in ('revise','void','recover'):raise DomainError('group_action','Choose revise, void or recover')
    if (action=='recover')!=(inv.state=='void'):raise DomainError('group_state','Recover a void group; otherwise revise/void an active group',409)
    if not str(p.get('source','')).strip() or not str(p.get('reason','')).strip():raise DomainError('source_required','Explicit group source and reason required')
    members=p.get('members',[])
    if len(members)!=len(tickets) or {m.get('ticket_id') for m in members}!={t.id for t in tickets}:raise DomainError('whole_group_required','Every current member exactly once is required',409)
    target_party=p.get('party_id',inv.party_id);party_member(db,target_party,inv.warehouse_id)
    owner_changed=target_party!=inv.party_id
    if owner_changed:
        require(actor,'finance.cross_party',inv.warehouse_id)
        evidence=p.get('owner_evidence') or {}
        old=finance.locked(db,Party,inv.party_id);new=finance.locked(db,Party,target_party)
        check_version(old,evidence.get('old_party_version'));check_version(new,evidence.get('new_party_version'))
        warehouses={inv.warehouse_id}|{t.warehouse_id for t in tickets}|{t.destination_warehouse_id for t in tickets if t.destination_warehouse_id}
        from app.finance.access import payment_warehouses
        for pay in payments:warehouses|=set(payment_warehouses(db,pay))
        if evidence.get('old_party_id')!=old.id or evidence.get('new_party_id')!=new.id or set(evidence.get('warehouse_ids',[]))!=warehouses or not str(evidence.get('source','')).strip():raise DomainError('owner_evidence','Exact old/new owner and all affected warehouses with source are required')
        for wh in warehouses:require(actor,'finance.cross_party',wh)
        if 'discount_total_ngn' not in p:raise DomainError('discount_source','New owner discount requires an explicit current amount')
    proposals=[];new_total=Decimal(0)
    for t in tickets:
        m=next(m for m in members if m['ticket_id']==t.id);check_version(t,m.get('expected_version'));changes=m.get('changes',{})
        fields(changes,packing.TICKET_FIELDS+' manual fx repricing reason')
        if 'party_id' in changes and changes['party_id']!=target_party:raise DomainError('group_owner','One current owner per shared invoice required')
        changes=dict(changes,party_id=target_party)
        native_change(db,actor,t,changes)
        proposed=SimpleNamespace(**{c.name:getattr(t,c.name) for c in t.__table__.columns})
        for k,v in packing.ticket_values(changes).items():setattr(proposed,k,v)
        proposed.version=t.version+1
        if proposed.transport!=db.get(Shipment,db.get(PackingDocument,t.document_id).shipment_id).transport:raise DomainError('transport_mismatch','Group and shipment transport differ')
        quote=pricing.apply(db,proposed,changes,pricing.snapshot(t),persist=False)
        allocation.revise(db,proposed,changes,quote,persist=False)
        for kind,rows in [('arrival',list(db.scalars(select(Arrival).where(Arrival.ticket_id==t.id)))),('physical',list(db.scalars(select(PhysicalRelease).where(PhysicalRelease.ticket_id==t.id))))]:
            for key in ('pieces','kg','cbm'):
                value=getattr(proposed,key);observed=known_sum(getattr(r,key) for r in rows)
                if rows and value is not None and (observed is None or observed>value):raise DomainError('group_quantity_review','Existing actual/unknown batches cannot be reduced or silently reconciled',409)
                if getattr(t,key) is not None and value is None:raise DomainError('group_quantity_review','Known source quantities cannot become unknown',409)
        if proposed.kg is None and not nonkg_sea(db,proposed) and action!='void':raise DomainError('quantities_unresolved','Only explicitly non-KG SEA may retain unknown original KG',409)
        if action!='void':
            charges=packing.charges(db,proposed)
            if charges is None:raise DomainError('group_pricing_review','Every member needs explicit original currency totals and actual FX evidence',409)
            new_total+=packing.invoice_amount(*charges,proposed.ngn_per_usd)
        proposals.append({'ticket_id':t.id,'expected_version':t.version,'changes':changes,'proposed':json_value({c.name:getattr(proposed,c.name) for c in t.__table__.columns}),'components':allocation.components(db,proposed)})
    if inv.kind=='AP':
        if 'amount_ngn' not in p:raise DomainError('payable_total_required','Explicit reviewed whole payable amount required')
        new_total=finance.decimal(p['amount_ngn']) if action!='void' else Decimal(0)
    if not tickets and inv.kind=='AR':
        if 'amount_ngn' not in p:raise DomainError('group_pricing_review','Standalone sourced bill needs explicit total')
        new_total=finance.decimal(p['amount_ngn']) if action!='void' else Decimal(0)
    original_discount=finance.total(db,Discount,'amount_ngn',invoice_id=inv.id)
    last=db.scalar(select(LegacyGroupRevision).where(LegacyGroupRevision.bill_id==bill.id,LegacyGroupRevision.action=='void').order_by(LegacyGroupRevision.created_at.desc()).limit(1)) if action=='recover' else None
    default_discount=Decimal(last.before['invoice']['discount_ngn']) if last else original_discount
    discount=Decimal(0) if action=='void' else finance.decimal(p.get('discount_total_ngn',str(default_discount)))
    if discount!=original_discount:require(actor,'finance.discount',inv.warehouse_id)
    if owner_changed and discount and not str(p.get('discount_source','')).strip():raise DomainError('discount_source','New-owner discount evidence required')
    restore_history=action=='recover' and target_party==app.historical_owner_party_id
    historical=Decimal(0) if action=='void' or owner_changed else bill.received if restore_history else app.applied_received
    historical_discount=Decimal(0) if action=='void' or owner_changed else bill.discount if restore_history else app.applied_discount
    excess=max(Decimal(0),historical+max(discount,historical_discount)-new_total)
    pending=excess>0 or discount<historical_discount or discount>new_total
    allocated=finance.total(db,Allocation,'amount_ngn',invoice_id=inv.id)
    refund=allocated if action=='void' or owner_changed else min(allocated,max(Decimal(0),allocated+historical+discount-new_total))
    if pending:refund=Decimal(0)
    if refund and p.get('refund_new_allocations') is not True:raise DomainError('refund_confirmation','Explicit reversal to original native payment owners required')
    cash=[{'payment_id':pay.id,'version':pay.version,'original_party_id':pay.party_id,'approval':pay.approval,'allocated_ngn':finance.total(db,Allocation,'amount_ngn',invoice_id=inv.id,payment_id=pay.id)} for pay in payments]
    return json_value({'bill_id':bill.id,'bill_version':bill.version,'invoice_version':inv.version,'application_version':app.version,'action':action,'members':proposals,
        'old_party_id':inv.party_id,'new_party_id':target_party,'owner_changed':owner_changed,'new_total_ngn':new_total,'current_discount_ngn':discount,
        'applied_received_ngn':historical,'applied_historical_discount_ngn':historical_discount,'frozen_received_ngn':bill.received-historical,'frozen_discount_ngn':bill.discount-historical_discount,
        'historical_owner_party_id':app.historical_owner_party_id,'historical_excess_ngn':excess,'pending_reconciliation':pending,'refund_native_ngn':refund,'native_payments':cash,
        'new_owner_outstanding_ngn':new_total-discount-historical-allocated+refund,'bank_proof_ids':[h.id for h in proofs],
        'context_facts_hash':digest(fact_snapshot(db)),'original_control':{'amount':bill.amount,'received':bill.received,'discount':bill.discount,'source_status':bill.source_status},'source':p['source'],'reason':p['reason']})


def confirm(db,actor,identity,p):
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    # Current authorization is required even for an idempotent replay.
    bill,inv,app,tickets,payments,proofs=current(db,actor,identity)
    if (p.get('owner_evidence') or {}).get('old_party_id')!=(p.get('owner_evidence') or {}).get('new_party_id'):
        for wh in p['owner_evidence'].get('warehouse_ids',[]):require(actor,'finance.cross_party',wh)
    def apply():
        reviewed=preview(db,actor,identity,p)
        if reviewed!=p.get('preview'):raise DomainError('preview_changed','Re-review full group, financial and irreversible facts',409,reviewed)
        if reviewed['pending_reconciliation']:raise DomainError('historical_balance_review','Unproven historical excess/discount requires source reconciliation; no credit is created',409,reviewed)
        before={'invoice':finance.invoice_json(db,inv),'application':finance.record(app),'tickets':[finance.record(t) for t in tickets]}
        if Decimal(reviewed['refund_native_ngn']):finance.unallocate(db,actor,inv,Decimal(reviewed['refund_native_ngn']),'Explicit whole-group revision')
        app.applied_received=Decimal(reviewed['applied_received_ngn']);app.applied_discount=Decimal(reviewed['applied_historical_discount_ngn']);app.state='void' if p['action']=='void' else 'active';app.version+=1
        inv.party_id=reviewed['new_party_id']
        for t in tickets:
            proposal=next(m for m in reviewed['members'] if m['ticket_id']==t.id);changes=proposal['changes'];before_price=pricing.snapshot(t)
            for k,v in packing.ticket_values(changes).items():setattr(t,k,v)
            quote=pricing.apply(db,t,changes,before_price)
            t.version+=1;t.review_state='pending';allocation.revise(db,t,changes,quote);t.state='void' if p['action']=='void' else 'active'
        db.flush()
        financial=finance.adjustment_preview(db,actor,inv.id,reviewed['new_total_ngn'],reviewed['current_discount_ngn'])
        db.info['legacy_group_revision']=inv.id
        try:
            finance.adjust_invoice(db,actor,dict(id=inv.id,expected_version=inv.version,amount_ngn=reviewed['new_total_ngn'],discount_total_ngn=reviewed['current_discount_ngn'],reason=p['reason'],preview=financial,idempotency_key=p['idempotency_key']+':invoice'))
        finally:db.info.pop('legacy_group_revision',None)
        inv.state='void' if p['action']=='void' else 'open';bill.version+=1
        for did in {t.document_id for t in tickets}:
            document=finance.locked(db,PackingDocument,did);document.version+=1
            states=set(db.scalars(select(Ticket.state).where(Ticket.document_id==did)))
            document.state='void' if states=={'void'} else 'active'
        if inv.kind=='AP':
            for ship in db.scalars(select(Shipment).where(Shipment.clearing_invoice_id==inv.id)):
                ship.clearing_party_id=inv.party_id;ship.clearing_amount_ngn=inv.amount_ngn;ship.version+=1
        db.flush();after={'invoice':finance.invoice_json(db,inv),'application':finance.record(app),'tickets':[finance.record(t) for t in tickets]}
        revision=LegacyGroupRevision(bill_id=bill.id,actor_id=actor.id,action=p['action'],source=p['source'],reason=p['reason'],before=before,after=after,preview=reviewed);db.add(revision);db.flush()
        audit(db,actor,'legacy.group_revised','legacy_bill',bill.id,before,{'revision_id':revision.id,'after':after})
        return {'id':bill.id,'version':bill.version,'revision_id':revision.id,'invoice':after['invoice'],'application':after['application'],'cash_created':False,'physical_facts_deleted':False}
    return run_command(db,actor,'legacy.group_revision',p.get('idempotency_key'),{'bill_id':identity,**p},apply)
