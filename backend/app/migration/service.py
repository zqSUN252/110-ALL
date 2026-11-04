"""Local imports use caller transactions; files are prepared before financial locks."""
from decimal import Decimal
from datetime import datetime
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select, func, text
from app.common import Actor, DomainError, require, check_version, audit, run_command, json_value
from app.models import (User,MigrationBatch,LegacyParty,LegacyShipment,LegacyTicket,LegacyBill,LegacyBillMember,
    LegacyPaymentObservation,LegacyF6,MigrationSourceMap,MigrationIssue,LegacyArchive,MigrationFile,LegacyResolution,
    Invoice,Payment,Discount,Allocation,Party,PartyWarehouse,Shipment,PackingDocument,Ticket,Warehouse)
from app.migration.source import digest
from app.finance.service import record, invoice_json

MODELS={'parties':LegacyParty,'shipments':LegacyShipment,'tickets':LegacyTicket,'bills':LegacyBill,
        'slips':LegacyPaymentObservation,'ledger':LegacyPaymentObservation,'f6':LegacyF6,'archive':LegacyArchive}


def stable(dataset,kind,key):return str(uuid5(NAMESPACE_URL,f'mcstar:{dataset}:{kind}:{key}'))


def plan_source(parsed,dataset,classification):
    if not dataset or len(dataset)>120:raise DomainError('dataset_required','Specify stable dataset namespace')
    if classification not in ('supplied_snapshot','synthetic'):raise DomainError('source_not_authorized','QA/history/unknown source cannot enter operational dataset')
    plan={'dataset':dataset,'classification':classification,'sha256':parsed['sha256'],'source_version':parsed['source_version'],'controls':parsed['controls'],'configuration_hash':parsed.get('configuration',{}).get('digest'),'issues_hash':digest(parsed['issues']),'mapping_hash':digest(parsed['maps']),'files_hash':digest([{k:v for k,v in f.items() if k!='bytes'} for f in parsed['files']]),'parser_version':2}
    return plan|{'plan_hash':digest(plan)}


def importer(db):
    identity=stable('system','actor','migration')
    actor=db.get(User,identity)
    if actor is None:
        actor=User(id=identity,username='__migration_nonlogin__',password_hash='!nonlogin',active=False)
        db.add(actor);db.flush()
    if actor.active:raise DomainError('importer_active','Technical importer must remain disabled',409)
    return Actor(actor.id,{'attachments.write'},set())


def apply_source(db,actor,parsed,plan,source_reference=None):
    require(actor,'*')
    if plan!=plan_source(parsed,plan['dataset'],plan['classification']):raise DomainError('plan_changed','Review plan again',409)
    dataset=plan['dataset']
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest(dataset)[:15],16)})
    existing=db.scalar(select(MigrationBatch).where(MigrationBatch.dataset==dataset,MigrationBatch.sha256==parsed['sha256']))
    if existing:
        if existing.plan_hash!=plan['plan_hash']:raise DomainError('rules_changed','Same source requires a reviewed mapping upgrade; no silent reapply',409)
        return {'batch_id':existing.id,'created':0,'state':existing.state}
    prior=db.scalar(select(MigrationBatch).where(MigrationBatch.dataset==dataset,MigrationBatch.state=='applied'))
    batch=MigrationBatch(dataset=dataset,sha256=parsed['sha256'],archive_sha256=(source_reference or {}).get('archive_sha256'),source_version=parsed['source_version'],classification=plan['classification'],state='review_required' if prior else 'applied',plan_hash=plan['plan_hash'],controls=parsed['controls'],source_reference=source_reference or {})
    db.add(batch);db.flush()
    if prior:
        # Row positions/fingerprints are observations, never cross-snapshot IDs.
        db.add(LegacyArchive(dataset=dataset,source_key=parsed['sha256'],batch_id=batch.id,pointer='/',raw=json_value({'entities':parsed['entities']}),kind='incremental_frozen',state='review_required'))
        db.add(MigrationIssue(batch_id=batch.id,pointer='/',code='cross_snapshot_identity_review'))
        db.flush();return {'batch_id':batch.id,'created':0,'state':batch.state}
    technical=importer(db)
    objects={k:{} for k in MODELS}; count=0
    tombs={}
    for row in parsed['entities']['archive']:
        if row['kind'] in ('billTomb','shipTomb','partnerTomb'):
            value=row['raw']['value'];tombs[row['kind']]=set(map(str,value if isinstance(value,(list,dict)) else []))
    for kind in ('parties','shipments','tickets','bills','slips','ledger','f6','archive'):
        for source in parsed['entities'][kind]:
            values=dict(source);key=values['source_key'];values|={'id':stable(dataset,kind,key),'dataset':dataset,'batch_id':batch.id}
            values.setdefault('state','unresolved')
            if kind=='tickets':values['shipment_id']=objects['shipments'][values.pop('shipment_key')].id
            if kind=='bills':
                party=objects['parties'].get(str(values['source_party_ref']))
                matches=[s for s in objects['shipments'].values() if s.source_no==values['source_shipment_ref'] and s.state!='tombstoned']
                ship=matches[0] if len(matches)==1 else None
                values|={'party_id':party.id if party and party.state!='tombstoned' else None,'shipment_id':ship.id if ship else None,'relation_state':'pending'}
                for missing,code in ((not values['party_id'],'missing_party'),(bool(values['source_shipment_ref']) and not ship,'missing_shipment')):
                    if missing:db.add(MigrationIssue(batch_id=batch.id,pointer=values['pointer'],code=code))
            if kind in ('slips','ledger'):
                bill=objects['bills'].get(str(values.pop('bill_key',None)))
                values['bill_id']=bill.id if bill else None
            tomb={'parties':'partnerTomb','shipments':'shipTomb','bills':'billTomb'}.get(kind)
            if tomb and key in tombs.get(tomb,set()):values['state']='tombstoned'
            obj=MODELS[kind](**values);db.add(obj);objects[kind][key]=obj;count+=1
        db.flush()
    for bill in objects['bills'].values():
        members,state=relation_candidates(db,bill)
        bill.relation_state=state
        for t in members:db.add(LegacyBillMember(bill_id=bill.id,ticket_id=t.id,source=bill.pointer+'/ticketKeys'))
        if state=='pending':db.add(MigrationIssue(batch_id=batch.id,pointer=bill.pointer,code='bill_relation_unresolved'))
    from app.attachments import put_file
    for file in parsed['files']:
        attachment=put_file(db,technical,file['bytes'],file['name'],file['media_type'],'legacy_batch',batch.id)
        db.add(MigrationFile(batch_id=batch.id,pointer=file['pointer'],attachment_id=attachment.id))
        for obj in objects['slips'].values():
            if file['pointer']==obj.pointer+'/file':obj.attachment_id=attachment.id
    entity_pointers={o.pointer:(o.__tablename__,o.id) for group in objects.values() for o in group.values()}
    for row in parsed['maps']:
        values=dict(row);parent=values['pointer']
        while parent and parent not in entity_pointers:parent=parent.rsplit('/',1)[0]
        values['target_type'],values['target_id']=entity_pointers.get(parent,('migration_batches',batch.id))
        db.add(MigrationSourceMap(batch_id=batch.id,**values))
    if parsed.get('configuration'):
        from app.models import AppSetting
        profile=parsed['configuration']
        for key,value in (('migration.warehouse_profiles',{'items':profile['warehouse_profiles']}),('documents.profile',profile['public'])):
            if db.scalar(select(AppSetting.id).where(AppSetting.key==key)):raise DomainError('configuration_conflict','Existing configuration requires explicit review',409)
            db.add(AppSetting(key=key,value=value))
        for pointer in profile['source_pointers']:
            db.add(MigrationSourceMap(batch_id=batch.id,pointer=pointer,target_type='app_settings',disposition='effective_prompt_configuration',value_hash=profile['digest']))
    for row in parsed['issues']:db.add(MigrationIssue(batch_id=batch.id,**row))
    audit(db,actor,'migration.applied','migration_batch',batch.id,None,{'sha256':batch.sha256,'controls':batch.controls})
    db.flush()
    return {'batch_id':batch.id,'created':count,'state':batch.state}


def legacy_access(actor,financial=False):
    require(actor,'legacy.global_read')
    if financial:require(actor,'finance.read')


def relation_candidates(db,bill):
    keys=bill.raw.get('ticketKeys') or ([bill.raw['ticketKey']] if bill.raw.get('ticketKey') else [])
    ships=list(db.scalars(select(LegacyShipment).where(LegacyShipment.dataset==bill.dataset,LegacyShipment.state!='tombstoned',LegacyShipment.source_no==bill.source_shipment_ref))) if bill.source_shipment_ref else []
    ids=[bill.shipment_id] if bill.shipment_id else [s.id for s in ships]
    members=list(db.scalars(select(LegacyTicket).where(LegacyTicket.shipment_id.in_(ids),LegacyTicket.receipt.in_(keys)))) if keys and ids else []
    complete=bool(keys) and bill.shipment_id is not None and set(keys)<={t.receipt for t in members}
    state='declared_membership' if complete else 'declared_shipment' if bill.kind=='AP' and bill.shipment_id and not keys else 'pending'
    return members,state


def upgrade_mapping(db,actor,parsed,plan,expected_plan_hash,source):
    require(actor,'*')
    if not str(source or '').strip() or plan!=plan_source(parsed,plan['dataset'],plan['classification']):raise DomainError('mapping_review_required','Reviewed parser-v2 plan and source required')
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(digest(plan['dataset'])[:15],16)})
    batch=db.scalar(select(MigrationBatch).where(MigrationBatch.dataset==plan['dataset'],MigrationBatch.sha256==parsed['sha256']).with_for_update())
    if not batch or batch.state!='applied':raise DomainError('batch_unavailable','Applied original batch required',409)
    if batch.plan_hash==plan['plan_hash']:return {'batch_id':batch.id,'added_members':0,'state':'mapping_verified'}
    if batch.plan_hash!=expected_plan_hash:raise DomainError('stale_plan','Original reviewed mapping changed',409)
    added=0
    for bill in db.scalars(select(LegacyBill).where(LegacyBill.batch_id==batch.id)):
        if bill.relation_state=='confirmed_membership':continue  # Explicit reviewed exclusions remain authoritative.
        members,state=relation_candidates(db,bill)
        existing=set(db.scalars(select(LegacyBillMember.ticket_id).where(LegacyBillMember.bill_id==bill.id)))
        for t in members:
            if t.id not in existing:
                db.add(LegacyBillMember(bill_id=bill.id,ticket_id=t.id,source=bill.pointer+'/ticketKeys:parser-v2-candidate'));added+=1
                if t.native_id:
                    native=db.get(Ticket,t.native_id)
                    if native.invoice_id and native.invoice_id!=bill.native_id:
                        db.add(MigrationIssue(batch_id=batch.id,pointer=t.pointer,code='existing_native_invoice_relation_conflict'))
        if bill.relation_state!='confirmed_membership':bill.relation_state=state
        bill.version+=1
    previous=batch.plan_hash;batch.plan_hash=plan['plan_hash'];batch.version+=1
    audit(db,actor,'migration.mapping_upgraded','migration_batch',batch.id,{'plan_hash':previous},{'plan_hash':batch.plan_hash,'parser_version':2,'added_members':added,'source':source})
    db.flush();return {'batch_id':batch.id,'added_members':added,'state':'mapping_verified'}


def resolve_members(db,actor,identity,payload):
    from app.finance.service import locked
    require(actor,'legacy.resolve');legacy_access(actor,True)
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    bill=locked(db,LegacyBill,identity)
    ap_ship=None
    if bill.kind=='AP':
        from sqlalchemy import or_
        candidate_ships=list(db.scalars(select(LegacyShipment).where(LegacyShipment.dataset==bill.dataset,or_(LegacyShipment.id==bill.shipment_id,LegacyShipment.source_no==bill.source_shipment_ref))))
        if payload.get('shipment_id'):
            ap_ship=locked(db,LegacyShipment,payload['shipment_id']);candidate_ships.append(ap_ship)
        for source_ship in candidate_ships:
            if source_ship.native_id:
                native=locked(db,Shipment,source_ship.native_id)
                for capability in ('shipments.manage','payables.manage','finance.read'):require(actor,capability,native.warehouse_id)
    authorized_ids=set(payload.get('ticket_ids') or [])|set(db.scalars(select(LegacyBillMember.ticket_id).where(LegacyBillMember.bill_id==bill.id)))
    for source_identity in authorized_ids:
        source_ticket=locked(db,LegacyTicket,source_identity)
        if source_ticket.native_id:
            ticket=locked(db,Ticket,source_ticket.native_id)
            require(actor,'packing.manage',ticket.warehouse_id);require(actor,'finance.adjust',ticket.warehouse_id)
    def apply():
        check_version(bill,payload.get('expected_version'))
        if bill.native_id:raise DomainError('legacy_group_review','Use complete group revision for already linked invoice membership',409)
        if not str(payload.get('source','')).strip() or not str(payload.get('reason','')).strip():raise DomainError('source_required','Complete membership identity evidence and excluded-key reason required')
        ids=payload.get('ticket_ids')
        standalone=payload.get('standalone') is True
        if not isinstance(ids,list) or (not ids and not standalone and not ap_ship) or len(set(ids))!=len(ids):raise DomainError('group_members','Explicit complete distinct rows or evidenced standalone bill required')
        if standalone and (ids or ap_ship):raise DomainError('group_members','Standalone has no shipment or ticket membership')
        if bill.kind=='AP':
            if not ap_ship and not standalone:raise DomainError('payable_shipment_required','Explicit source shipment/version or evidenced standalone payable required')
            if ap_ship:
                check_version(ap_ship,payload.get('shipment_version'))
                if ap_ship.dataset!=bill.dataset or ap_ship.state=='tombstoned':raise DomainError('source_scope','AP shipment source differs or is tombstoned')
                if ap_ship.native_id and db.get(Shipment,ap_ship.native_id).clearing_invoice_id:raise DomainError('existing_native_invoice','Source shipment already has a payable; reconcile it first',409)
        elif payload.get('shipment_id') or payload.get('shipment_version'):raise DomainError('group_members','Shipment selection applies to AP source resolution')
        selected=[locked(db,LegacyTicket,i) for i in ids]
        for t in selected:
            check_version(t,payload.get('member_versions',{}).get(t.id))
            if t.dataset!=bill.dataset:raise DomainError('source_scope','Member source dataset differs')
            if ap_ship and t.shipment_id!=ap_ship.id:raise DomainError('source_scope','AP members must belong to the explicitly selected source shipment')
            if t.native_id:
                native=locked(db,Ticket,t.native_id);require(actor,'packing.manage',native.warehouse_id);require(actor,'finance.adjust',native.warehouse_id)
                if bill.kind=='AR' and native.invoice_id:raise DomainError('existing_native_invoice','Existing native bill must be reconciled before membership confirmation',409)
        before=list(db.scalars(select(LegacyBillMember).where(LegacyBillMember.bill_id==bill.id)))
        for m in before:
            old=db.get(LegacyTicket,m.ticket_id)
            if old.native_id:
                native=db.get(Ticket,old.native_id)
                require(actor,'packing.manage',native.warehouse_id);require(actor,'finance.adjust',native.warehouse_id)
                if bill.kind=='AR' and native.invoice_id:raise DomainError('existing_native_invoice','Covered native bill must be reconciled before changing reservation',409)
        old_ids=[m.ticket_id for m in before]
        old_ship=bill.shipment_id
        if bill.kind=='AP':bill.shipment_id=ap_ship.id if ap_ship else None
        for m in before:db.delete(m)
        db.flush()
        for t in selected:db.add(LegacyBillMember(bill_id=bill.id,ticket_id=t.id,source=payload['source']))
        bill.relation_state='confirmed_membership';bill.version+=1
        db.add(LegacyResolution(entity_type='bill_members',entity_id=bill.id,source_version=bill.version-1,actor_id=actor.id,source=payload['source'],links={'before':old_ids,'after':ids,'reason':payload['reason'],'original_declared_keys':bill.raw.get('ticketKeys'),'previous_shipment_id':old_ship,'shipment_id':bill.shipment_id,'shipment_version':ap_ship.version if ap_ship else None,'standalone':standalone,'original_shipment_ref':bill.source_shipment_ref}))
        audit(db,actor,'legacy.members_resolved','legacy_bill',bill.id,{'members':old_ids},{'members':ids,'source':payload['source']})
        db.flush();return {'id':bill.id,'version':bill.version,'relation_state':bill.relation_state,'members':ids}
    return run_command(db,actor,'legacy.resolve_members',payload.get('idempotency_key'),{'id':identity,**payload},apply)


def historical_received(db,invoice_id):
    invoice=db.get(Invoice,invoice_id)
    if not invoice or not (invoice.source_key or '').startswith('legacy:'):return Decimal('0.00')
    from app.models import LegacyBillApplication
    value=db.scalar(select(LegacyBillApplication.applied_received).join(LegacyBill,LegacyBill.id==LegacyBillApplication.bill_id).where(LegacyBill.native_id==invoice_id))
    if value is None:raise DomainError('legacy_control_missing','Historical invoice is missing its required typed source control',409)
    return value.quantize(Decimal('.01'))


def historical_discount(db,invoice_id):
    invoice=db.get(Invoice,invoice_id)
    if not invoice or not (invoice.source_key or '').startswith('legacy:'):return Decimal('0.00')
    from app.models import LegacyBillApplication
    value=db.scalar(select(LegacyBillApplication.applied_discount).join(LegacyBill,LegacyBill.id==LegacyBillApplication.bill_id).where(LegacyBill.native_id==invoice_id))
    if value is None:raise DomainError('legacy_control_missing','Historical invoice is missing its required typed source control',409)
    return value.quantize(Decimal('.01'))


def resolve_party(db,actor,identity,payload):
    require(actor,'legacy.resolve');require(actor,'legacy.global_read')
    from app.finance.service import locked
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    obj=locked(db,LegacyParty,identity)
    for cap in ('parties.manage','parties.read'):require(actor,cap,payload['warehouse_id'])
    if not db.get(Warehouse,payload['warehouse_id']):raise DomainError('warehouse_required','Known warehouse required')
    def apply():
        check_version(obj,payload.get('expected_version'))
        if obj.native_id or obj.state=='tombstoned':raise DomainError('legacy_state','Already resolved or tombstoned',409)
        if not payload.get('source'):raise DomainError('source_required','Source evidence required')
        party=Party(warehouse_id=payload['warehouse_id'],name=obj.name,phone=obj.phone,address=obj.address,classification=obj.classification,source='legacy:'+obj.id)
        db.add(party);db.flush();db.add(PartyWarehouse(party_id=party.id,warehouse_id=party.warehouse_id,source=payload['source']))
        obj.native_id=party.id;obj.state='resolved';obj.version+=1
        db.add(LegacyResolution(entity_type='party',entity_id=obj.id,source_version=obj.version-1,actor_id=actor.id,source=payload['source'],links={'party_id':party.id,'warehouse_id':party.warehouse_id}))
        audit(db,actor,'legacy.party_resolved','legacy_party',obj.id,None,{'native_id':party.id})
        db.flush();return {'id':obj.id,'version':obj.version,'native_id':party.id}
    return run_command(db,actor,'legacy.resolve_party',payload.get('idempotency_key'),{'id':identity,**payload},apply)


def resolve_bill(db,actor,identity,payload):
    require(actor,'legacy.resolve');require(actor,'legacy.global_read');require(actor,'finance.read')
    from app.finance.access import party_member
    from app.finance.service import locked,day
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    obj=locked(db,LegacyBill,identity)
    for cap in ('finance.read','finance.opening'):require(actor,cap,payload['warehouse_id'])
    party_member(db,payload['party_id'],payload['warehouse_id'])
    def apply():
        check_version(obj,payload.get('expected_version'))
        if obj.native_id or obj.state=='tombstoned':raise DomainError('legacy_state','Already resolved or tombstoned',409)
        if not str(payload.get('source','')).strip():raise DomainError('source_required','Source evidence required')
        if obj.relation_state=='pending':raise DomainError('legacy_group_review','Complete source bill relationships must be confirmed before native linkage',409)
        if obj.currency!='NGN' or any(v!=v.quantize(Decimal('.01')) for v in (obj.amount,obj.received,obj.discount)):
            raise DomainError('legacy_money_review','Currency or subcent control requires explicit review',409)
        if obj.received+obj.discount>obj.amount:raise DomainError('legacy_money_review','Source balance conflict requires explicit review',409)
        # A bill's source date is not proof of its actual upload date.
        uploaded=day(payload['uploaded_on']) if payload.get('uploaded_on') else day(obj.raw['date']) if obj.kind=='AP' and obj.raw.get('date') else None
        due=day(payload.get('due_on') or obj.raw['dueDate']) if payload.get('due_on') or obj.raw.get('dueDate') else None
        if uploaded is None or due is None:raise DomainError('legacy_dates_required','Confirm original upload/due dates before native resolution',409)
        dates_source=payload.get('dates_source') or (obj.pointer+'/date + /dueDate' if obj.kind=='AP' and not payload.get('uploaded_on') and not payload.get('due_on') else None)
        if not str(dates_source or '').strip():raise DomainError('legacy_dates_required','Actual business date evidence required',409)
        if due<uploaded:raise DomainError('legacy_dates_required','Due date cannot precede actual upload date')
        inv=Invoice(warehouse_id=payload['warehouse_id'],party_id=payload['party_id'],scope='live',kind=obj.kind,reference=obj.reference,
            amount_ngn=obj.amount,uploaded_on=uploaded,due_on=due,source_key='legacy:'+obj.id,pricing={'legacy_control_id':obj.id,'bank_state':'unknown','date_semantics':'original_bill_date' if obj.kind=='AP' else 'confirmed_actual_upload'},state='open')
        if payload.get('period') is not None:
            if obj.kind!='AP':raise DomainError('invalid_kind','Settlement period applies to AP')
            require(actor,'payables.manage',inv.warehouse_id)
            inv.period=checked_period(payload['period'])
        db.add(inv);db.flush();obj.native_id=inv.id;obj.state='resolved';obj.version+=1
        from app.models import LegacyBillApplication
        db.add(LegacyBillApplication(bill_id=obj.id,historical_owner_party_id=inv.party_id,applied_received=obj.received,applied_discount=obj.discount,state='active'));db.flush()
        if obj.kind=='AP' and obj.shipment_id:
            source_ship=db.get(LegacyShipment,obj.shipment_id)
            if source_ship.native_id:
                ship=db.get(Shipment,source_ship.native_id)
                if ship.clearing_invoice_id or ship.warehouse_id!=inv.warehouse_id:raise DomainError('legacy_relation_conflict','Existing payable or warehouse conflicts',409)
                ship.clearing_invoice_id=inv.id;ship.clearing_party_id=inv.party_id;ship.clearing_amount_ngn=obj.amount;ship.clearing_on=uploaded;ship.version+=1
        for legacy in db.scalars(select(LegacyTicket).join(LegacyBillMember,LegacyBillMember.ticket_id==LegacyTicket.id).where(LegacyBillMember.bill_id==obj.id)) if obj.kind=='AR' else []:
            if legacy.native_id:
                ticket=db.get(Ticket,legacy.native_id)
                if ticket.warehouse_id!=inv.warehouse_id or ticket.invoice_id:raise DomainError('legacy_relation_conflict','Native cargo ownership or invoice conflicts',409)
                ticket.invoice_id=inv.id;ticket.party_id=inv.party_id;ticket.version+=1
        if obj.discount:db.add(Discount(invoice_id=inv.id,amount_ngn=obj.discount,reason='Sourced historical discount',actor_id=actor.id))
        db.add(LegacyResolution(entity_type='bill',entity_id=obj.id,source_version=obj.version-1,actor_id=actor.id,source=payload['source'],links={'invoice_id':inv.id,'party_id':inv.party_id,'warehouse_id':inv.warehouse_id,'uploaded_on':str(uploaded),'due_on':str(due),'dates_source':dates_source,'date_semantics':inv.pricing['date_semantics']}))
        audit(db,actor,'legacy.bill_resolved','legacy_bill',obj.id,None,{'native_id':inv.id,'source':payload['source']})
        db.flush();return {'id':obj.id,'version':obj.version,'native_id':inv.id,'invoice':invoice_json(db,inv)}
    return run_command(db,actor,'legacy.resolve_bill',payload.get('idempotency_key'),{'id':identity,**payload},apply)


def checked_period(value):
    import re
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',value):
        raise DomainError('period_required','Explicit YYYY-MM settlement period required')
    return value


def resolve_period(db,actor,identity,payload):
    from app.finance.service import locked,invalidate
    require(actor,'legacy.resolve');legacy_access(actor,True)
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    bill=locked(db,LegacyBill,identity)
    if bill.kind!='AP' or not bill.native_id:raise DomainError('legacy_period_state','Resolve native AP ownership first',409)
    invoice=locked(db,Invoice,bill.native_id)
    for cap in ('finance.read','payables.manage'):require(actor,cap,invoice.warehouse_id)
    def apply():
        check_version(bill,payload.get('expected_version'));check_version(invoice,payload.get('invoice_version'))
        if not str(payload.get('source','')).strip():raise DomainError('source_required','Settlement-period evidence required')
        before=invoice.period;invoice.period=checked_period(payload.get('period'));invoice.version+=1;bill.version+=1
        db.add(LegacyResolution(entity_type='bill_period',entity_id=bill.id,source_version=bill.version-1,actor_id=actor.id,source=payload['source'],links={'invoice_id':invoice.id,'old_period':before,'period':invoice.period}))
        invalidate(db,invoice_id=invoice.id)
        audit(db,actor,'legacy.period_resolved','invoice',invoice.id,{'period':before},{'period':invoice.period,'source':payload['source']})
        db.flush();return {'id':bill.id,'version':bill.version,'invoice_version':invoice.version,'period':invoice.period}
    return run_command(db,actor,'legacy.resolve_period',payload.get('idempotency_key'),{'id':identity,**payload},apply)


def resolve_shipment(db,actor,identity,payload):
    require(actor,'legacy.resolve');require(actor,'legacy.global_read');require(actor,'finance.read')
    from app.finance.service import locked,day
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    obj=locked(db,LegacyShipment,identity)
    for cap in ('shipments.manage','packing.manage','packing.read'):require(actor,cap,payload['warehouse_id'])
    if not db.get(Warehouse,payload['warehouse_id']):raise DomainError('warehouse_required','Known warehouse required')
    def apply():
        check_version(obj,payload.get('expected_version'))
        if obj.native_id or obj.state=='tombstoned':raise DomainError('legacy_state','Already resolved or tombstoned',409)
        if not str(payload.get('source','')).strip():raise DomainError('source_required','Source evidence required')
        physical=payload.get('physical_reference') or None
        s=Shipment(warehouse_id=payload['warehouse_id'],reference=physical or '',transport=obj.transport,destination=obj.destination,state='legacy')
        db.add(s);db.flush()
        payables=list(db.scalars(select(LegacyBill).where(LegacyBill.shipment_id==obj.id,LegacyBill.kind=='AP',LegacyBill.native_id.is_not(None))))
        if len(payables)>1:raise DomainError('legacy_relation_conflict','Multiple source payables require reconciliation',409)
        if payables:
            invoice=db.get(Invoice,payables[0].native_id)
            if invoice.warehouse_id!=s.warehouse_id:raise DomainError('legacy_relation_conflict','Payable warehouse conflicts',409)
            s.clearing_invoice_id=invoice.id;s.clearing_party_id=invoice.party_id;s.clearing_amount_ngn=invoice.amount_ngn;s.clearing_on=invoice.uploaded_on
        d=PackingDocument(warehouse_id=s.warehouse_id,shipment_id=s.id,reference=obj.source_no,uploaded_on=day(payload['uploaded_on']))
        db.add(d);db.flush()
        for source in db.scalars(select(LegacyTicket).where(LegacyTicket.shipment_id==obj.id).order_by(LegacyTicket.position)):
            if any(v is not None and v!=v.quantize(Decimal('.01')) for v in (source.freight_usd,source.clearing_ngn)):
                raise DomainError('legacy_money_review','Ticket source subcent charge requires explicit review',409)
            bills=list(db.scalars(select(LegacyBill).join(LegacyBillMember,LegacyBillMember.bill_id==LegacyBill.id).where(LegacyBillMember.ticket_id==source.id,LegacyBill.kind=='AR')))
            resolved=[db.get(Invoice,b.native_id) for b in bills if b.native_id]
            if len(resolved)>1 or any(i.warehouse_id!=s.warehouse_id for i in resolved):raise DomainError('legacy_relation_conflict','Multiple invoice or warehouse conflict requires review',409)
            inv=resolved[0] if resolved else None
            t=Ticket(document_id=d.id,warehouse_id=s.warehouse_id,party_id=inv.party_id if inv else None,position=source.position,receipt=source.receipt,
                mark=source.consignee,item_zh=source.description,contact_raw=source.contact,address=source.raw.get('addr'),destination=source.destination,
                transport=obj.transport,pieces=source.pieces,kg=source.kg,cbm=source.cbm,freight_usd=source.freight_usd,clearing_ngn=source.clearing_ngn,
                pricing_authority='review',invoice_id=inv.id if inv else None)
            db.add(t);db.flush();source.native_id=t.id;source.state='resolved';source.version+=1
            from app.models import TicketAttachment
            for link in db.scalars(select(MigrationFile).where(MigrationFile.batch_id==source.batch_id,MigrationFile.pointer.in_([source.pointer+'/originalFile',obj.pointer+'/originalFile']))):
                db.add(TicketAttachment(ticket_id=t.id,attachment_id=link.attachment_id))
        obj.native_id=s.id;obj.physical_reference=physical;obj.state='resolved';obj.version+=1
        db.add(LegacyResolution(entity_type='shipment',entity_id=obj.id,source_version=obj.version-1,actor_id=actor.id,source=payload['source'],links={'shipment_id':s.id,'document_id':d.id,'physical_reference':physical}))
        audit(db,actor,'legacy.shipment_resolved','legacy_shipment',obj.id,None,{'native_id':s.id,'source':payload['source']})
        db.flush();return {'id':obj.id,'version':obj.version,'native_id':s.id,'document_id':d.id}
    return run_command(db,actor,'legacy.resolve_shipment',payload.get('idempotency_key'),{'id':identity,**payload},apply)


def ticket_legacy_bills(db,ticket_id):
    return list(db.scalars(select(LegacyBill).join(LegacyBillMember,LegacyBillMember.bill_id==LegacyBill.id).join(LegacyTicket,LegacyTicket.id==LegacyBillMember.ticket_id).where(LegacyTicket.native_id==ticket_id,LegacyBill.kind=='AR')))


def guard_legacy_ticket(db,ticket_id,changes=None,destructive=False):
    bills=ticket_legacy_bills(db,ticket_id)
    financial={'pieces','kg','cbm','freight_usd','clearing_ngn','ngn_per_usd','manual','fx','repricing','charge_components','discount_total_ngn','party_id'}
    changed=financial.intersection(changes or {})
    if bills and changed:
        from app.packing.service import ticket_values
        ticket=db.get(Ticket,ticket_id);normalized=ticket_values(changes)
        changed={key for key in changed if key not in normalized or getattr(ticket,key)!=normalized[key]}
    if bills and (destructive or changed):
        raise DomainError('legacy_group_review','Historical group bill requires a separate group financial resolution',409)
    return bool(bills)


def resolve_charge_basis(db,actor,identity,payload):
    require(actor,'legacy.resolve');require(actor,'legacy.global_read')
    from app.finance.service import locked
    from app.packing import allocation
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    ticket=locked(db,Ticket,identity)
    for cap in ('packing.manage','packing.read','finance.read'):require(actor,cap,ticket.warehouse_id)
    if not ticket_legacy_bills(db,ticket.id):raise DomainError('legacy_required','Expected sourced historical cargo')
    def apply():
        check_version(ticket,payload.get('expected_version'))
        lines=allocation.normalize(payload['charge_components'])
        if allocation.totals(lines)!=(ticket.freight_usd,ticket.clearing_ngn):raise DomainError('legacy_charge_mismatch','Review may explain units, never rewrite historical charge controls')
        ticket.version+=1
        allocation.revise(db,ticket,{'charge_components':lines})
        audit(db,actor,'legacy.charge_basis_reviewed','ticket',ticket.id,None,{'source':payload['source'],'components':lines})
        return {'id':ticket.id,'version':ticket.version,'pricing_authority':ticket.pricing_authority}
    return run_command(db,actor,'legacy.charge_basis',payload.get('idempotency_key'),{'id':identity,**payload},apply)


def legacy_list(db,actor,kind,limit=50,offset=0,query=''):
    legacy_access(actor,kind in ('bills','slips','ledger','tickets'))
    if kind not in MODELS or kind=='archive':raise DomainError('invalid_kind','Unknown business collection')
    cls=MODELS[kind];q=select(cls)
    if kind in ('slips','ledger'):q=q.where(cls.kind==('slip' if kind=='slips' else 'ledger'))
    name={'parties':'name','shipments':'source_no','tickets':'receipt','bills':'reference','f6':'receipt'}.get(kind)
    if query and name:q=q.where(getattr(cls,name).ilike('%'+query+'%'))
    count=db.scalar(select(func.count()).select_from(q.subquery()))
    rows=[]
    for obj in db.scalars(q.order_by(cls.id).offset(offset).limit(min(limit,200))):
        value=record(obj);value.pop('raw',None)
        if kind=='bills':
            value|=json_value({'outstanding':obj.amount-obj.received-obj.discount,'bank_state':'unknown','historical_received':obj.received,'members':list(db.scalars(select(LegacyBillMember.ticket_id).where(LegacyBillMember.bill_id==obj.id)))})
        rows.append(value)
    return {'items':rows,'total':count,'source':'supplied_snapshot','warehouse_ownership':'unresolved_global_only'}


def legacy_detail(db,actor,kind,identity):
    legacy_access(actor,kind in ('bills','slips','ledger'))
    if kind not in MODELS or kind=='archive':raise DomainError('invalid_kind','Unknown business collection')
    row=db.get(MODELS[kind],identity)
    if not row:raise DomainError('not_found','Legacy record not found',404)
    value=record(row)
    # Raw logistics can include fees; financial fields require finance.read too.
    require(actor,'finance.read')
    value['issues']=[record(i) for i in db.scalars(select(MigrationIssue).where(MigrationIssue.batch_id==row.batch_id,MigrationIssue.pointer.startswith(row.pointer)))]
    if kind=='bills':
        value['members']=[record(m) for m in db.scalars(select(LegacyBillMember).where(LegacyBillMember.bill_id==row.id))]
        invoice=db.get(Invoice,row.native_id) if row.native_id else None
        if invoice and ('*' in actor.capabilities or invoice.warehouse_id in actor.warehouse_ids):
            from app.models import LegacyBillApplication
            application=db.scalar(select(LegacyBillApplication).where(LegacyBillApplication.bill_id==row.id))
            value['application']=record(application) if application else None
            value['invoice']=invoice_json(db,invoice)
            value['period_state']='confirmed' if invoice.period else 'pending' if invoice.kind=='AP' else 'not_applicable'
    return value


def verify_source(db,parsed,batch_id):
    batch=db.get(MigrationBatch,batch_id)
    if not batch or batch.sha256!=parsed['sha256'] or batch.state!='applied':raise DomainError('batch_unverified','No applied matching snapshot')
    counts={}
    for kind,cls in MODELS.items():
        q=select(func.count()).select_from(cls).where(cls.batch_id==batch_id)
        if kind in ('slips','ledger'):q=q.where(cls.kind==('slip' if kind=='slips' else 'ledger'))
        counts[kind]=db.scalar(q)
    actual={b.source_key:b for b in db.scalars(select(LegacyBill).where(LegacyBill.batch_id==batch_id))}
    money=all(all(getattr(actual[b['source_key']],f)==b[f] for f in ('amount','received','discount','currency','source_status')) for b in parsed['entities']['bills'])
    from app.attachments import read_file
    files=list(db.scalars(select(MigrationFile).where(MigrationFile.batch_id==batch_id)))
    from app.models import Attachment
    by_pointer={f.pointer:db.get(Attachment,f.attachment_id) for f in files}
    files_ok=all(f['pointer'] in by_pointer and __import__('hashlib').sha256(read_file(db.info['settings'],by_pointer[f['pointer']])).hexdigest()==f['sha256'] for f in parsed['files'])
    relations_ok=True;edge_count=0
    for bill in actual.values():
        members=set(db.scalars(select(LegacyBillMember.ticket_id).where(LegacyBillMember.bill_id==bill.id)));edge_count+=len(members)
        if bill.relation_state=='confirmed_membership':
            proof=db.scalar(select(LegacyResolution).where(LegacyResolution.entity_type=='bill_members',LegacyResolution.entity_id==bill.id).order_by(LegacyResolution.created_at.desc()).limit(1))
            relations_ok &= bool(proof) and members==set(proof.links['after'])
            if bill.kind=='AP':relations_ok &= bool(proof) and bill.shipment_id==proof.links.get('shipment_id')
        else:
            expected,state=relation_candidates(db,bill)
            relations_ok &= members=={t.id for t in expected} and bill.relation_state==state
    files_ok &= len(files)==len(parsed['files'])
    return {'counts':counts,'counts_match':all(counts[k]==parsed['controls'][k] for k in counts),'money_status_match':money,'files_match':files_ok,'relation_members':edge_count,'relations_match':relations_ok,'parser_version':2,'source_sha256':parsed['sha256'],'state':'verified' if money and files_ok and relations_ok and all(counts[k]==parsed['controls'][k] for k in counts) else 'failed'}
