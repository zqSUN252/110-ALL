"""Native billing membership, arrived-charge controls and explicit source redistribution.

Original invoices, cash and claims are never replaced. A regroup decision reverses
each selected allocation and applies only the operator's specified destinations.
"""
from decimal import Decimal
from collections import defaultdict
from sqlalchemy import select,text
from app.common import DomainError,require,check_version,json_value,audit
from app.models import (Ticket,PackingDocument,NativeBillingGroup,NativeGroupRevision,
    Invoice,Allocation,Discount,Payment,Arrival,InvoiceAdjustment,BankClaim)
from app.finance import service as finance
from app.finance.access import payment_access
from app.cargo.commands import command,preview_access,member,fields


def members(db,group,overrides=None):
    rows=list(db.scalars(select(Ticket).where(Ticket.billing_group_id==group.id).order_by(Ticket.id)))
    return [(overrides or {}).get(t.id,t) for t in rows]


def snapshot(db,group):
    return finance.record(group)|{'members':[{'ticket_id':t.id,'version':t.version,'state':t.state,'invoice_id':t.invoice_id} for t in members(db,group)]}


def eligible_match(a,b):
    return bool(a.party_id and a.party_id==b.party_id and ((a.mark and a.mark==b.mark) or
        (a.phone_confirmed and b.phone_confirmed and a.phone_normalized and a.phone_normalized==b.phone_normalized)))


def conflicts(rows):
    phones=sorted({t.phone_normalized for t in rows if t.phone_confirmed and t.phone_normalized})
    destinations=sorted({(t.destination or '',t.destination_warehouse_id or '') for t in rows})
    return {'parties':sorted({t.party_id for t in rows if t.party_id}), 'confirmed_phones':phones,
            'destinations':[{'destination':d,'warehouse_id':w or None} for d,w in destinations],
            'requires_attribution':len({t.party_id for t in rows})>1 or len(phones)>1 or len(destinations)>1}


def has_financial_facts(db,invoice_id):
    return bool(invoice_id and (db.scalar(select(Allocation.id).where(Allocation.invoice_id==invoice_id).limit(1)) or
        db.scalar(select(Discount.id).where(Discount.invoice_id==invoice_id).limit(1))))


def attach(db,actor,t):
    """Automatic same-identity grouping only before any allocated/discount facts."""
    from app.migration.service import ticket_legacy_bills
    if t.billing_group_id or ticket_legacy_bills(db,t.id):return
    candidates=list(db.scalars(select(Ticket).where(Ticket.document_id==t.document_id,Ticket.state=='active',Ticket.id!=t.id).order_by(Ticket.id)))
    peers=[r for r in candidates if r.billing_group_id and eligible_match(t,r)]
    ids={r.billing_group_id for r in peers}
    if len(ids)==1:
        g=finance.locked(db,NativeBillingGroup,next(iter(ids)))
        rows=members(db,g)
        invoice=db.get(Invoice,g.invoice_id) if g.invoice_id else None
        # A manual/legacy invoice has no group arrival basis. Merging a new
        # member into it would leave that member's arrived charge unbilled.
        # A single-ticket automatic invoice may still become the stable group
        # invoice once same-identity members join.
        managed=invoice is None or bool((invoice.pricing or {}).get('arrival_allocation',{}).get('group_id')) or (invoice.source_key or '').startswith('ticket:')
        if managed and not conflicts(rows+[t])['requires_attribution'] and not has_financial_facts(db,g.invoice_id):
            require(actor,'packing.manage',g.warehouse_id)
            before=snapshot(db,g);t.billing_group_id=g.id;t.invoice_id=g.invoice_id;g.version+=1;db.flush()
            db.add(NativeGroupRevision(document_id=t.document_id,actor_id=actor.id,reason='Automatic same confirmed party and Mark or confirmed phone',before=before,after=snapshot(db,g)))
            return
    g=NativeBillingGroup(document_id=t.document_id,warehouse_id=t.warehouse_id,party_id=t.party_id,invoice_id=t.invoice_id)
    db.add(g);db.flush();t.billing_group_id=g.id;db.flush()
    db.add(NativeGroupRevision(document_id=t.document_id,actor_id=actor.id,reason='Explicit native source membership',before={},after=snapshot(db,g)))


def basis(db,group,overrides=None):
    from app.finance.rounding import cargo_basis
    from . import allocation
    rows=[]
    for t in members(db,group,overrides):
        if t.state!='active':continue
        arrived=bool(db.scalar(select(Arrival.id).where(Arrival.ticket_id==t.id)))
        if not arrived or not allocation.ready(db,t):
            if arrived:raise DomainError('group_price_unresolved','Confirm each arrived member price before group billing',409)
            rows.append({'ticket_id':t.id,'ticket_version':t.version,'unarrived':True});continue
        rows.append(cargo_basis(db,t))
    return json_value({'group_id':group.id,'group_version':group.version,'warehouse_id':group.warehouse_id,'party_id':group.party_id,
        'members':rows,'amount_ngn':sum((Decimal(r.get('amount_ngn','0')) for r in rows),Decimal(0)),
        'rounding_adjustment_ngn':sum((Decimal(r.get('rounding_adjustment_ngn','0')) for r in rows),Decimal(0))})


def group_access(db,actor,g):
    from .service import financial_access
    require(actor,'packing.manage',g.warehouse_id);require(actor,'packing.read',g.warehouse_id)
    for t in members(db,g):
        preview_access(db,actor,Ticket,t.id,'packing.manage');financial_access(db,actor,t)


def sync(db,actor,t,p,automatic=False):
    g=finance.locked(db,NativeBillingGroup,t.billing_group_id);rows=members(db,g)
    if len(rows)==1 and not (g.invoice_id and (db.get(Invoice,g.invoice_id).pricing or {}).get('arrival_allocation',{}).get('group_id')):return False
    group_access(db,actor,g)
    if not any(db.scalar(select(Arrival.id).where(Arrival.ticket_id==r.id)) for r in rows):return True
    current=basis(db,g);amount=current['amount_ngn']
    if not g.invoice_id:
        if not g.party_id:raise DomainError('party_unresolved','Confirm group party before AR')
        d=db.get(PackingDocument,g.document_id)
        result=finance.create_invoice(db,actor,dict(warehouse_id=g.warehouse_id,party_id=g.party_id,reference=d.reference,
            uploaded_on=d.uploaded_on.isoformat(),amount_ngn=amount,source_key='group:'+g.id,rounding_basis=current,idempotency_key=p['idempotency_key']+':invoice'))
        g.invoice_id=result['id']
        for r in rows:r.invoice_id=g.invoice_id
        db.flush()
    else:
        inv=finance.locked(db,Invoice,g.invoice_id)
        previous=(inv.pricing or {}).get('arrival_allocation')
        if automatic and previous is None:
            # Legacy/manual totals stay authoritative; a single-ticket
            # automatic invoice upgrades to the group basis once members join.
            if not (inv.source_key or '').startswith('ticket:'):return True
        def charge_sources(value):
            if isinstance(value,list):return [charge_sources(v) for v in value]
            if isinstance(value,dict):return {k:charge_sources(v) for k,v in value.items() if k not in ('ticket_version','group_version')}
            return value
        if charge_sources(previous)==charge_sources(current) and inv.amount_ngn==Decimal(amount):return True
        if automatic and previous is not None and (inv.amount_ngn!=Decimal(previous['amount_ngn']) or inv.rounding_adjustment_ngn!=Decimal(previous['rounding_adjustment_ngn'])):
            raise DomainError('invoice_basis_changed','Review independently changed group invoice before arrival',409)
        pv=finance.adjustment_preview(db,actor,inv.id,amount,p.get('discount_total_ngn'),current) if automatic else p.get('finance_preview')
        payload=dict(id=inv.id,expected_version=inv.version,amount_ngn=amount,rounding_basis=current,
            reason=p.get('reason') or 'Actual arrived native group charges',preview=pv,idempotency_key=p['idempotency_key']+':invoice')
        if p.get('discount_total_ngn') is not None:payload['discount_total_ngn']=p['discount_total_ngn']
        finance.adjust_invoice(db,actor,payload)
    return True


def read_sources(db,actor,identity):
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    d=preview_access(db,actor,PackingDocument,identity,'packing.manage')
    if d.state!='active':raise DomainError('group_document','Recover document before grouping',409)
    from app.migration.service import ticket_legacy_bills,historical_received
    from .service import financial_access
    from app.documents.release import cargo_hold_state
    rows=list(db.scalars(select(Ticket).where(Ticket.document_id==d.id,Ticket.state=='active').order_by(Ticket.id)))
    native=[]
    for t in rows:
        if ticket_legacy_bills(db,t.id):continue
        preview_access(db,actor,Ticket,t.id,'packing.manage');financial_access(db,actor,t);native.append(t)
    invoices=[finance.locked(db,Invoice,i) for i in sorted({t.invoice_id for t in native if t.invoice_id})]
    for inv in invoices:
        require(actor,'finance.adjust',inv.warehouse_id)
        if historical_received(db,inv.id) or (inv.source_key or '').startswith('legacy:'):raise DomainError('legacy_group_review','Historical controls require their existing source review',409)
        # A document cannot redistribute a shared invoice whose other members are hidden/outside scope.
        others=list(db.scalars(select(Ticket).where(Ticket.invoice_id==inv.id,Ticket.state=='active')))
        if {t.id for t in others}-{t.id for t in native}:raise DomainError('group_scope','Select the whole existing invoice membership',409)
    ids=[i.id for i in invoices]
    allocations=list(db.scalars(select(Allocation).where(Allocation.invoice_id.in_(ids)).order_by(Allocation.id))) if ids else []
    discounts=list(db.scalars(select(Discount).where(Discount.invoice_id.in_(ids)).order_by(Discount.id))) if ids else []
    payments=[finance.locked(db,Payment,i) for i in sorted({r.payment_id for r in allocations})]
    for pay in payments:payment_access(db,actor,pay,'finance.adjust');payment_access(db,actor,pay,'finance.post')
    source_alloc=[]
    for r in allocations:
        remaining=r.amount_ngn+sum((x.amount_ngn for x in allocations if x.reverses_id==r.id),Decimal(0))
        if r.amount_ngn>0 and remaining>0:source_alloc.append(finance.record(r)|{'remaining_ngn':str(remaining),'payer_id':db.get(Payment,r.payment_id).party_id})
    groups=[finance.locked(db,NativeBillingGroup,i) for i in sorted({t.billing_group_id for t in native if t.billing_group_id})]
    return d,native,{'document':finance.record(d),'tickets':[finance.record(t) for t in native],
        'groups':[snapshot(db,g) for g in groups], 'invoices':[finance.invoice_json(db,i) for i in invoices],
        'allocations':source_alloc,'allocation_history':[finance.record(r) for r in allocations],
        'discounts':[{'invoice_id':i.id,'amount_ngn':str(finance.total(db,Discount,'amount_ngn',invoice_id=i.id))} for i in invoices if finance.total(db,Discount,'amount_ngn',invoice_id=i.id)],
        'discount_history':[finance.record(r) for r in discounts], 'payments':[finance.payment_json(db,p) for p in payments],
        'claims':[finance.record(c) for c in db.scalars(select(BankClaim).where(BankClaim.payment_id.in_([p.id for p in payments])).order_by(BankClaim.id))] if payments else [],
        'controls':[{'ticket_id':t.id,'holds':cargo_hold_state(db,t)} for t in native]}


def preview(db,actor,identity,plan):
    d,tickets,sources=read_sources(db,actor,identity)
    if not plan:return json_value(sources|{'requires_plan':True,'targets':[]})
    fields(plan,'groups allocations discounts attribution_source confirmed_conflicts')
    proposed=plan.get('groups');by_id={t.id:t for t in tickets};seen=set();keys=set();retained=set();targets=[]
    if not isinstance(proposed,list) or not proposed or len(proposed)>200:raise DomainError('group_plan','Choose complete group membership and sources')
    source_invoices={i['id']:i for i in sources['invoices']}
    from app.finance.rounding import cargo_basis
    from . import allocation
    for row in proposed:
        fields(row,'key ticket_ids invoice_id party_id')
        key=row.get('key');ids=row.get('ticket_ids')
        if not isinstance(key,str) or not key or len(key)>80 or key in keys or not isinstance(ids,list) or not ids or len(set(ids))!=len(ids) or any(i not in by_id or i in seen for i in ids):raise DomainError('group_plan','Choose distinct complete memberships and target names')
        keys.add(key);seen.update(ids);selected=[by_id[i] for i in sorted(ids)];conflict=conflicts(selected)
        party=row.get('party_id') or (selected[0].party_id if len({t.party_id for t in selected})==1 else None)
        if not party:raise DomainError('party_unresolved','Explicit confirmed target party required')
        member(db,actor,party,d.warehouse_id,'packing.manage')
        if conflict['requires_attribution'] or any(t.party_id!=party for t in selected):
            if plan.get('confirmed_conflicts') is not True or not str(plan.get('attribution_source') or '').strip():raise DomainError('group_conflicts','Explicit conflict review and attribution evidence required')
            require(actor,'finance.cross_party',d.warehouse_id)
        iid=row.get('invoice_id')
        if iid:
            if iid not in source_invoices or iid in retained or source_invoices[iid]['party_id']!=party:raise DomainError('group_invoice','Retain a distinct existing invoice with the explicitly selected party')
            retained.add(iid)
        amount=Decimal(0);components=[]
        for t in selected:
            if db.scalar(select(Arrival.id).where(Arrival.ticket_id==t.id)):
                if not allocation.ready(db,t):raise DomainError('group_price_unresolved','Confirm original component charges before regrouping',409)
                b=cargo_basis(db,t);amount+=Decimal(b['amount_ngn']);components.append(b)
        targets.append({'key':key,'ticket_ids':sorted(ids),'invoice_id':iid,'party_id':party,'amount_ngn':str(amount),'components':components,'conflicts':conflict})
    if seen!=set(by_id):raise DomainError('group_plan','Plan must include every active native member in this document')
    totals={k:{'allocated':Decimal(0),'discount':Decimal(0)} for k in keys}
    for field,identity_key,sourcerows,value_key,destination_field in [('allocations','allocation_id',sources['allocations'],'remaining_ngn','allocated'),('discounts','invoice_id',sources['discounts'],'amount_ngn','discount')]:
        expected={(r['id'] if field=='allocations' else r['invoice_id']):r for r in sourcerows};received=plan.get(field,[])
        if not isinstance(received,list) or len(received)!=len(expected) or {r.get(identity_key) for r in received}!=set(expected):raise DomainError('group_source_plan','Complete explicit source allocation and discount destinations required')
        for r in received:
            fields(r,identity_key+' targets');source=expected[r[identity_key]];parts=r.get('targets');total=Decimal(0);used=set()
            if not isinstance(parts,list) or not parts:raise DomainError('group_source_plan','Specify each source destination; balance is allowed only for original payment')
            for part in parts:
                fields(part,'key amount_ngn');key=part.get('key');value=finance.decimal(part.get('amount_ngn'),positive=True)
                if key in used or (key not in keys and not (key is None and field=='allocations')):raise DomainError('group_source_plan','Choose a valid distinct target group or original payer balance')
                used.add(key);total+=value
                if key is not None:
                    totals[key][destination_field]+=value
                    if field=='allocations':
                        target=next(x for x in targets if x['key']==key)
                        if source['payer_id']!=target['party_id']:
                            pay=db.get(Payment,source['payment_id']);payment_access(db,actor,pay,'finance.cross_party');require(actor,'finance.cross_party',d.warehouse_id)
                            if not str(plan.get('attribution_source') or '').strip():raise DomainError('cross_party_confirmation','Original payer authorization evidence required')
            if total!=Decimal(source[value_key]):raise DomainError('group_source_plan','Each source total must be conserved exactly')
    for target in targets:
        values=totals[target['key']]
        if values['allocated']+values['discount']>Decimal(target['amount_ngn']):raise DomainError('group_over_allocation','Explicit allocations and discounts exceed target current receivable')
        target.update(allocated_ngn=str(values['allocated']),discount_ngn=str(values['discount']),outstanding_ngn=str(Decimal(target['amount_ngn'])-values['allocated']-values['discount']))
    return json_value(sources|{'requires_plan':False,'plan':plan,'targets':targets,'invalidate_unexecuted_instructions':True})


@command('document.regroup','id expected_version plan preview reason','packing.manage',PackingDocument,financial=True)
def confirm(db,actor,p):
    current=preview(db,actor,p['id'],p['plan']);d=finance.locked(db,PackingDocument,p['id']);check_version(d,p.get('expected_version'))
    if current.get('requires_plan') or p.get('preview')!=current:raise DomainError('preview_changed','Complete financial source plan changed; review again',409)
    if not str(p.get('reason') or '').strip():raise DomainError('reason_required','Explicit regrouping reason required')
    source_invoices=[finance.locked(db,Invoice,i['id']) for i in current['invoices']]
    # Reverse exactly the selected source allocation, never choose by payment amount/name.
    for row in current['allocations']:
        db.add(Allocation(payment_id=row['payment_id'],invoice_id=row['invoice_id'],amount_ngn=-Decimal(row['remaining_ngn']),reverses_id=row['id'],reason=p['reason'],actor_id=actor.id))
        pay=finance.locked(db,Payment,row['payment_id']);pay.version+=1
    for row in current['discounts']:
        inv=db.get(Invoice,row['invoice_id']);require(actor,'finance.discount',inv.warehouse_id)
        db.add(Discount(invoice_id=inv.id,amount_ngn=-Decimal(row['amount_ngn']),reason=p['reason'],actor_id=actor.id))
    db.flush()
    old_groups=[finance.locked(db,NativeBillingGroup,r['id']) for r in current['groups']];used_groups=set();targets={}
    for target in current['targets']:
        ts=[finance.locked(db,Ticket,i) for i in target['ticket_ids']]
        g=next((g for g in old_groups if g.id not in used_groups and (g.invoice_id==target['invoice_id'] if target['invoice_id'] else {t.id for t in members(db,g)}==set(target['ticket_ids']))),None)
        if not g:g=NativeBillingGroup(document_id=d.id,warehouse_id=d.warehouse_id,party_id=target['party_id']);db.add(g);db.flush()
        else:g.version+=1
        used_groups.add(g.id);g.party_id=target['party_id'];g.state='active'
        inv=db.get(Invoice,target['invoice_id']) if target['invoice_id'] else None
        if inv is None:
            # The original upload date is the source of the existing 14-day rule;
            # inherit an old due date when splitting an already billed source.
            result=finance.create_invoice(db,actor,dict(warehouse_id=d.warehouse_id,party_id=g.party_id,reference=d.reference,
                uploaded_on=d.uploaded_on.isoformat(),amount_ngn=target['amount_ngn'],source_key='group:'+g.id,idempotency_key=p['idempotency_key']+':'+target['key']))
            inv=finance.locked(db,Invoice,result['id'])
            dates={db.get(Invoice,t.invoice_id).due_on for t in ts if t.invoice_id}
            if len(dates)>1:raise DomainError('group_due_source','Select an existing invoice when source due dates differ')
            if dates:inv.due_on=next(iter(dates))
        else:
            db.add(InvoiceAdjustment(invoice_id=inv.id,old_amount=inv.amount_ngn,new_amount=Decimal(target['amount_ngn']),reason=p['reason'],actor_id=actor.id));inv.version+=1
            inv.amount_ngn=Decimal(target['amount_ngn'])
        inv.state='open';inv.charge_usd=None;inv.charge_ngn=None;inv.ngn_per_usd=None;g.invoice_id=inv.id
        for t in ts:t.billing_group_id=g.id;t.invoice_id=inv.id;t.version+=1
        targets[target['key']]=(g,inv)
    for g in old_groups:
        if g.id not in used_groups:g.state='superseded';g.version+=1
    # Any unretained invoice becomes historical zero-current-AR; identity is preserved.
    retained={inv.id for _,inv in targets.values()}
    for inv in source_invoices:
        if inv.id not in retained:
            db.add(InvoiceAdjustment(invoice_id=inv.id,old_amount=inv.amount_ngn,new_amount=Decimal(0),reason=p['reason'],actor_id=actor.id))
            inv.amount_ngn=Decimal(0);inv.state='merged';inv.version+=1;inv.charge_usd=None;inv.charge_ngn=None;inv.ngn_per_usd=None;inv.rounding_adjustment_ngn=Decimal(0)
        finance.invalidate(db,invoice_id=inv.id)
    db.flush()
    from app.finance.rounding import save_basis
    for g,inv in targets.values():
        b=basis(db,g);inv.rounding_adjustment_ngn=Decimal(b['rounding_adjustment_ngn']);save_basis(db,actor,inv,b)
    for row in p['plan'].get('discounts',[]):
        for part in row['targets']:
            inv=targets[part['key']][1];require(actor,'finance.discount',inv.warehouse_id)
            db.add(Discount(invoice_id=inv.id,amount_ngn=Decimal(part['amount_ngn']),reason=p['reason'],actor_id=actor.id))
    db.flush()
    for row in p['plan'].get('allocations',[]):
        source=next(x for x in current['allocations'] if x['id']==row['allocation_id']);pay=finance.locked(db,Payment,source['payment_id'])
        for part in row['targets']:
            if part['key'] is None:continue
            inv=targets[part['key']][1];cross=None
            if inv.party_id!=pay.party_id:cross={'source':p['plan']['attribution_source'],'beneficiaries':[{'invoice_id':inv.id,'party_id':inv.party_id}]}
            finance.allocate(db,actor,pay,[dict(invoice_id=inv.id,expected_version=inv.version,amount_ngn=part['amount_ngn'])],cross)
    for _,inv in targets.values():finance.invalidate(db,invoice_id=inv.id)
    d.version+=1;db.flush()
    after={'groups':[snapshot(db,g) for g,_ in targets.values()],'invoices':[finance.invoice_json(db,inv) for _,inv in targets.values()]}
    db.add(NativeGroupRevision(document_id=d.id,actor_id=actor.id,reason=p['reason'],before=current,after=json_value(after)))
    return {'id':d.id,'warehouse_id':d.warehouse_id,'version':d.version,'command_key':p['idempotency_key'],**after}
