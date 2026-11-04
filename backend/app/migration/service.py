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
    plan={'dataset':dataset,'classification':classification,'sha256':parsed['sha256'],'source_version':parsed['source_version'],'controls':parsed['controls'],'configuration_hash':parsed.get('configuration',{}).get('digest'),'issues_hash':digest(parsed['issues']),'mapping_hash':digest(parsed['maps']),'files_hash':digest([{k:v for k,v in f.items() if k!='bytes'} for f in parsed['files']]),'parser_version':1}
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
        keys=bill.raw.get('ticketKeys') or ([bill.raw['ticketKey']] if bill.raw.get('ticketKey') else [])
        members=[t for t in objects['tickets'].values() if t.shipment_id==bill.shipment_id and t.receipt in keys]
        found={t.receipt for t in members}
        if keys and set(keys)<=found:
            # Owning source recalc filters ALL matching rows by receipt membership.
            bill.relation_state='declared_membership'
            for t in members:db.add(LegacyBillMember(bill_id=bill.id,ticket_id=t.id,source=bill.pointer+'/ticketKeys'))
        elif bill.kind=='AP' and bill.shipment_id:bill.relation_state='declared_shipment'
        else:db.add(MigrationIssue(batch_id=batch.id,pointer=bill.pointer,code='bill_relation_unresolved'))
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


def historical_received(db,invoice_id):
    invoice=db.get(Invoice,invoice_id)
    if not invoice or not (invoice.source_key or '').startswith('legacy:'):return Decimal('0.00')
    value=db.scalar(select(LegacyBill.received).where(LegacyBill.native_id==invoice_id))
    if value is None:raise DomainError('legacy_control_missing','Historical invoice is missing its required typed source control',409)
    return value.quantize(Decimal('.01'))


def historical_discount(db,invoice_id):
    invoice=db.get(Invoice,invoice_id)
    if not invoice or not (invoice.source_key or '').startswith('legacy:'):return Decimal('0.00')
    value=db.scalar(select(LegacyBill.discount).where(LegacyBill.native_id==invoice_id))
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
        if obj.currency!='NGN' or any(v!=v.quantize(Decimal('.01')) for v in (obj.amount,obj.received,obj.discount)):
            raise DomainError('legacy_money_review','Currency or subcent control requires explicit review',409)
        if obj.received+obj.discount>obj.amount:raise DomainError('legacy_money_review','Source balance conflict requires explicit review',409)
        # Original dates only. No import-day substitution for an absent business date.
        uploaded=day(obj.raw['date']) if obj.raw.get('date') else None
        due=day(obj.raw['dueDate']) if obj.raw.get('dueDate') else None
        if uploaded is None or due is None:raise DomainError('legacy_dates_required','Confirm original upload/due dates before native resolution',409)
        inv=Invoice(warehouse_id=payload['warehouse_id'],party_id=payload['party_id'],scope='live',kind=obj.kind,reference=obj.reference,
            amount_ngn=obj.amount,uploaded_on=uploaded,due_on=due,source_key='legacy:'+obj.id,pricing={'legacy_control_id':obj.id,'bank_state':'unknown'},state='open')
        db.add(inv);db.flush();obj.native_id=inv.id;obj.state='resolved';obj.version+=1
        if obj.kind=='AP' and obj.shipment_id:
            source_ship=db.get(LegacyShipment,obj.shipment_id)
            if source_ship.native_id:
                ship=db.get(Shipment,source_ship.native_id)
                if ship.clearing_invoice_id or ship.warehouse_id!=inv.warehouse_id:raise DomainError('legacy_relation_conflict','Existing payable or warehouse conflicts',409)
                ship.clearing_invoice_id=inv.id;ship.clearing_party_id=inv.party_id;ship.clearing_amount_ngn=obj.amount;ship.clearing_on=uploaded;ship.version+=1
        for legacy in db.scalars(select(LegacyTicket).join(LegacyBillMember,LegacyBillMember.ticket_id==LegacyTicket.id).where(LegacyBillMember.bill_id==obj.id)):
            if legacy.native_id:
                ticket=db.get(Ticket,legacy.native_id)
                if ticket.warehouse_id!=inv.warehouse_id or ticket.invoice_id:raise DomainError('legacy_relation_conflict','Native cargo ownership or invoice conflicts',409)
                ticket.invoice_id=inv.id;ticket.party_id=inv.party_id;ticket.version+=1
        if obj.discount:db.add(Discount(invoice_id=inv.id,amount_ngn=obj.discount,reason='Sourced historical discount',actor_id=actor.id))
        db.add(LegacyResolution(entity_type='bill',entity_id=obj.id,source_version=obj.version-1,actor_id=actor.id,source=payload['source'],links={'invoice_id':inv.id,'party_id':inv.party_id,'warehouse_id':inv.warehouse_id}))
        audit(db,actor,'legacy.bill_resolved','legacy_bill',obj.id,None,{'native_id':inv.id,'source':payload['source']})
        db.flush();return {'id':obj.id,'version':obj.version,'native_id':inv.id,'invoice':invoice_json(db,inv)}
    return run_command(db,actor,'legacy.resolve_bill',payload.get('idempotency_key'),{'id':identity,**payload},apply)


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
            bills=list(db.scalars(select(LegacyBill).join(LegacyBillMember,LegacyBillMember.bill_id==LegacyBill.id).where(LegacyBillMember.ticket_id==source.id)))
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
    return list(db.scalars(select(LegacyBill).join(LegacyBillMember,LegacyBillMember.bill_id==LegacyBill.id).join(LegacyTicket,LegacyTicket.id==LegacyBillMember.ticket_id).where(LegacyTicket.native_id==ticket_id)))


def guard_legacy_ticket(db,ticket_id,changes=None,destructive=False):
    bills=ticket_legacy_bills(db,ticket_id)
    financial={'pieces','kg','cbm','freight_usd','clearing_ngn','ngn_per_usd','manual','fx','repricing','charge_components','discount_total_ngn','party_id'}
    if bills and (destructive or financial.intersection(changes or {})):
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
    return {'counts':counts,'counts_match':all(counts[k]==parsed['controls'][k] for k in counts),'money_status_match':money,'files_match':files_ok and len(files)==len(parsed['files']),'source_sha256':parsed['sha256'],'state':'verified' if money and files_ok and all(counts[k]==parsed['controls'][k] for k in counts) else 'failed'}
