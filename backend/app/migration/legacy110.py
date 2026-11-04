"""Known 110 adapter; explicitly mapped accounts, original observations, inert queues.

Input is an acquired, scrubbed private export, never a live SQLite path. Old
verification occupies an entry as disputed until current evidence is revalidated.
"""
import json
from datetime import date,datetime
from decimal import Decimal
from sqlalchemy import select, text
from app.common import require, check_version, DomainError, audit, json_value
from app.models import (MigrationBatch, MigrationSourceMap, MigrationIssue, LegacyArchive,
    BankAccount, BankAccountWarehouse, BankFile, BankEntry, BankSourceRow, BankClaim,
    EvidenceCase, EvidenceReview, EvidenceImage, Delivery, Attachment, Warehouse,ChannelMessage)
from app.migration.source import digest
from app.migration.service import stable, importer


KNOWN = {'bank_files','bank_imports','bank_rows','bank_claims','workflow_cases',
    'workflow_ledger','workflow_reviews','workflow_outbox','workflow_inbox',
    'workflow_exports','workflow_bank_dates','messages','connection_config',
    'recovery_state','recovery_attempts','image_summary_config','image_summary_jobs'}


def obj(value):
    if isinstance(value, dict): return value
    try: return json.loads(value or '{}')
    except (ValueError, TypeError): return {}


def native_plan(source, dataset, mapping):
    if source.get('classification') not in ('synthetic','production_supplied'):
        raise DomainError('source_classification_required','Explicit supplied source required')
    if not dataset or len(dataset)>120 or not mapping.get('source'):
        raise DomainError('mapping_source_required','Reviewed owner/account mapping evidence required')
    value={'dataset':dataset,'source_hash':digest(source),'mapping':mapping,'adapter_version':1}
    return value|{'plan_hash':digest(value)}


def apply_native(db, actor, source, plan):
    require(actor,'*')
    if native_plan(source,plan['dataset'],plan['mapping'])!=plan:
        raise DomainError('plan_changed','Review exact source and mapping again',409)
    dataset=plan['dataset'];mapping=plan['mapping']
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    old=db.scalar(select(MigrationBatch).where(MigrationBatch.dataset==dataset))
    if old:
        if old.plan_hash!=plan['plan_hash']:raise DomainError('cross_snapshot_identity_review','Changed source or mapping requires separate review',409)
        return {'batch_id':old.id,'created':0,'state':old.state,'identity_conflict_entries':old.controls.get('identity_conflict_entries',0)}
    warehouse=db.get(Warehouse,mapping['warehouse_id'])
    if not warehouse:raise DomainError('warehouse_required','Explicit known warehouse required')
    rows=source['rows'];native={};archives={};technical=importer(db)
    batch=MigrationBatch(dataset=dataset,sha256=plan['source_hash'],source_version='110-sqlite-v1',classification=source['classification'],state='applied',plan_hash=plan['plan_hash'],controls={'tables':{k:len(v) for k,v in rows.items()}},source_reference={'source':source['source'],'snapshot_sha256':source['snapshot_sha256'],'mapping_evidence':mapping['source']})
    db.add(batch);db.flush()
    def issue(pointer,code):db.add(MigrationIssue(batch_id=batch.id,pointer=pointer,code=code))
    def key(table, row):return str(row.get('id',row.get('bank_row_id','')))
    for table,values in rows.items():
        for entry in values:
            raw=entry['raw'];identity=entry['identity']
            archived=LegacyArchive(dataset=dataset,source_key=table+':'+identity,batch_id=batch.id,pointer=entry['pointer'],raw=raw,kind='110:'+table,state='historical_nonexecutable')
            db.add(archived);db.flush();archives[(table,key(table,raw))]=archived
            if table not in KNOWN:issue(entry['pointer'],'unknown_110_table_archived')
    imports={str(x['raw']['id']):x['raw'] for x in rows.get('bank_imports',[])}
    accounts={}
    for import_id,raw in imports.items():
        confirmed=mapping.get('imports',{}).get(import_id)
        if not confirmed:raise DomainError('account_mapping_required','Every bank import requires an explicitly reviewed native account')
        account=db.get(BankAccount,confirmed['account_id']);check_version(account,confirmed['expected_version']) if account else None
        if not account or account.currency!=raw.get('currency') or account.scope!=mapping['scope']:
            raise DomainError('account_mapping_conflict','Account currency/scope mismatch')
        if not db.scalar(select(BankAccountWarehouse.id).where(BankAccountWarehouse.account_id==account.id,BankAccountWarehouse.warehouse_id==warehouse.id,BankAccountWarehouse.active.is_(True))):
            raise DomainError('account_membership_required','Confirmed account must belong to warehouse')
        accounts[import_id]=account
    files={}
    for iid,raw in imports.items():
        fid=str(raw['file_id']);account=accounts[iid]
        attachment_id=mapping.get('bank_files',{}).get(fid)
        attachment=db.get(Attachment,attachment_id) if attachment_id else None
        original=next((v['raw'] for v in rows.get('bank_files',[]) if str(v['raw']['id'])==fid),None)
        if not attachment or not original or attachment.sha256!=original.get('sha256') or attachment.created_by!=actor.id:
            raise DomainError('original_file_required','Upload and confirm hash-matching original bank file before native apply')
        existing=db.scalar(select(BankFile).where(BankFile.account_id==account.id,BankFile.sha256==attachment.sha256))
        if existing:raise DomainError('existing_bank_file_review','Existing file requires explicit identity reconciliation',409)
        if (fid,account.id) not in files:
            value=BankFile(account_id=account.id,attachment_id=attachment.id,sha256=attachment.sha256,state='imported',mapping={'legacy_batch':batch.id,'original_file_id':fid})
            db.add(value);db.flush();files[(fid,account.id)]=value
    entries={}
    for item in rows.get('bank_rows',[]):
        raw=item['raw'];iid=str(raw['import_id']);account=accounts[iid]
        amounts=[Decimal(str(raw.get(k) or 0)) for k in ('credit','debit')]
        if any(not n.is_finite() or n<0 or n!=n.quantize(Decimal('.01')) for n in amounts):raise DomainError('money_review','Bank amount cannot be silently rounded')
        parsed_date=None
        if raw.get('transaction_date'):
            try:parsed_date=date.fromisoformat(str(raw['transaction_date']))
            except ValueError:issue(item['pointer'],'bank_date_unresolved')
        entry=BankEntry(account_id=account.id,currency=account.currency,credit=amounts[0],debit=amounts[1],transaction_date=parsed_date,trusted_id=None,reference=raw.get('reference') or '',description=raw.get('description') or '',fingerprint=digest({'dataset':dataset,'row':raw['id']}),issues=['historical_source_requires_review'])
        db.add(entry);db.flush();entries[str(raw['id'])]=entry;native[item['pointer']]=entry
        imp=imports[iid];values=raw.get('raw_values');values=obj(values) if isinstance(values,str) else values
        if not isinstance(values,list):values=[values]
        db.add(BankSourceRow(file_id=files[(str(imp['file_id']),account.id)].id,entry_id=entry.id,sheet=imp.get('sheet') or '',row_number=int(raw['source_row']),raw_values=values,parsed={'legacy_batch':batch.id,'source_row':raw}))
    messages={}
    for item in rows.get('messages',[]):
        raw=item['raw'];channel=mapping.get('channel_instance')
        try:
            sent=datetime.fromisoformat(raw.get('sent_at') or '')
            if not sent.tzinfo:raise ValueError()
            if not channel or not raw.get('session') or not raw.get('message_id') or not raw.get('group_id') or not raw.get('sender'):raise ValueError()
        except (ValueError,TypeError):
            issue(item['pointer'],'channel_source_mapping_required');continue
        message=ChannelMessage(instance=channel,session=raw['session'],source_id=raw['message_id'],group_id=raw['group_id'],sender_id=raw['sender'],sent_at=sent,warehouse_id=warehouse.id,scope=mapping['scope'],generation='legacy:'+batch.id,body=raw.get('caption'),reply_to=raw.get('reply_to_id'),body_hash=digest(raw.get('caption')),state='historical',from_me=bool(raw.get('from_me')),origin_state='historical')
        db.add(message);db.flush();messages[str(raw['id'])]=message;native[item['pointer']]=message
    cases={}
    for item in rows.get('workflow_cases',[]):
        raw=item['raw'];cid=str(raw['id']);confirm=mapping.get('cases',{}).get(cid)
        if not confirm:raise DomainError('case_mapping_required','Every known case requires an account and warehouse scope confirmation')
        account=db.get(BankAccount,confirm['account_id'])
        if account is None or account.id not in {a.id for a in accounts.values()} or raw.get('scope')!=mapping['scope']:
            raise DomainError('case_scope_conflict','Historical case scope/account requires review')
        result=obj(raw.get('result_json'));fields=result.get('normalized_fields') or result.get('candidate_fields') or result.get('fields') or {}
        manual=obj(raw.get('manual_json'));fields={**fields,**(manual.get('corrections') or {})}
        # Only whitelisted scalar fields; original source/result/manual remain immutable archives.
        values={k:fields[k] for k in ('currency','payer_name','transaction_reference','recipient_account','bank_name','business_reference') if isinstance(fields.get(k),str)}
        amount=fields.get('amount')
        if amount is not None:
            try:
                number=Decimal(str(amount))
                if number.is_finite() and number>=0 and number==number.quantize(Decimal('.01')):values['amount']=number
            except Exception:issue(item['pointer'],'case_amount_unresolved')
        if fields.get('transaction_date'):
            try:values['transaction_date']=date.fromisoformat(str(fields['transaction_date']))
            except ValueError:issue(item['pointer'],'case_date_unresolved')
        case=EvidenceCase(warehouse_id=warehouse.id,scope=mapping['scope'],account_id=account.id,state='historical_review',manual_fields=list((manual.get('corrections') or {}).keys()),**values)
        db.add(case);db.flush();cases[cid]=case;native[item['pointer']]=case
        image_id=mapping.get('case_images',{}).get(cid)
        if image_id:
            image=db.get(Attachment,image_id)
            if not image or image.created_by!=actor.id or image.sha256!=raw.get('sha256'):raise DomainError('source_image_conflict','Case source image must match original hash')
            message=messages.get(str(raw.get('message_id')))
            db.add(EvidenceImage(case_id=case.id,attachment_id=image.id,message_id=message.id if message else None,source='legacy110:'+batch.id,context={'source_key':raw.get('source_key'),'source_json':obj(raw.get('source_json'))}))
        else:issue(item['pointer'],'original_case_image_missing')
    occupied={}
    for item in rows.get('workflow_ledger',[]):
        raw=item['raw']
        if raw.get('state')=='confirmed':occupied.setdefault(str(raw['bank_row_id']),[]).append((str(raw['case_id']),raw))
    for item in rows.get('bank_claims',[]):
        raw=item['raw'];rid=str(raw['bank_row_id'])
        # bank_claim alone is not a workflow case ID; explicit mapping prevents guessed roots.
        cid=mapping.get('claims',{}).get(rid)
        if rid not in occupied:
            if not cid:raise DomainError('claim_mapping_required','Occupied source row must retain an explicitly identified case')
            occupied[rid]=[(str(cid),raw)]
    for rid,owners in occupied.items():
        if len({x[0] for x in owners})!=1:raise DomainError('claim_conflict','Conflicting historical owners need source adjudication',409)
        cid,raw=owners[0];entry=entries.get(rid);case=cases.get(cid)
        if not entry or not case or case.account_id!=entry.account_id:raise DomainError('claim_mapping_conflict','Claim ownership/account mapping conflicts')
        if db.scalar(select(BankClaim.id).where(BankClaim.case_id==case.id,BankClaim.state!='released')):raise DomainError('claim_conflict','Multiple active source rows require explicit reconciliation')
        # Restore every original owner as a frozen fact, not a new verification.
        # Identity candidates must exist before the controlled adjudication API
        # can distinguish them. All operational claim paths still use its guard.
        claim=BankClaim(entry_id=entry.id,case_id=case.id,state='disputed',reason='Historical occupied source; current evidence revalidation required',actor_id=technical.id)
        db.add(claim);case.state='disputed';db.flush()
        db.add(EvidenceReview(case_id=case.id,actor_id=technical.id,action='legacy_import',previous_version=case.version,fields={'original_claim':raw,'batch_id':batch.id},reason='Original actor is source text, not current authorization'))
        # Same old fingerprint is an unresolved transaction-identity diagnostic, never merged cash.
        source_row=next(x['raw'] for x in rows['bank_rows'] if str(x['raw']['id'])==rid)
        for other in rows['bank_rows']:
            r=other['raw'];candidate=entries[str(r['id'])]
            if candidate.id!=entry.id and candidate.account_id==entry.account_id and source_row.get('fingerprint') and r.get('fingerprint')==source_row['fingerprint']:
                candidate.issues=list(candidate.issues)+['legacy_occupied_identity_conflict']
    for item in rows.get('workflow_reviews',[]):
        raw=item['raw'];case=cases.get(str(raw.get('case_id')))
        if case:db.add(EvidenceReview(case_id=case.id,actor_id=technical.id,action='legacy_review',previous_version=case.version,fields={'original_review':raw,'batch_id':batch.id},reason='Historical review provenance; no bank cash posted'))
    for item in rows.get('workflow_outbox',[]):
        raw=item['raw'];state='uncertain' if raw.get('state') in ('uncertain','sending','inflight') else 'historical'
        delivery=Delivery(actor_id=technical.id,session='legacy-disabled',recipient='',text='',state=state,boundary={'legacy_batch':batch.id,'archive_id':archives[('workflow_outbox',str(raw['id']))].id,'nonexecutable':True})
        db.add(delivery);db.flush();native[item['pointer']]=delivery
    for pointer,row in native.items():
        archived=next(a for a in archives.values() if a.pointer==pointer);archived.state='linked_historical'
    archive_pointers={a.pointer:a for a in archives.values()}
    for item in source['maps']:
        pointer=item['pointer'];parent=pointer
        while parent and parent not in native and parent not in archive_pointers:parent=parent.rsplit('/',1)[0]
        target=native.get(parent) or archive_pointers.get(parent,batch)
        db.add(MigrationSourceMap(batch_id=batch.id,pointer=pointer,target_type=target.__tablename__,target_id=target.id,disposition='historical_nonexecutable',value_hash=item['value_hash']))
    from app.bank.identity import guard_claim
    conflicts=0
    for item in rows.get('bank_rows',[]):
        entry=entries[str(item['raw']['id'])]
        try:guard_claim(db,entry)
        except DomainError as error:
            if error.code!='legacy_identity_review':raise
            entry.issues=sorted(set(entry.issues)|{'legacy_occupied_identity_conflict'})
            issue(item['pointer'],'legacy_occupied_identity_conflict');conflicts+=1
    batch.controls={**batch.controls,'identity_conflict_entries':conflicts}
    for account in {a.id:a for a in accounts.values()}.values():account.revision+=1
    db.flush()
    # A later historical observation also invalidates an existing normal/native
    # verification when its current identity or relevant bank bucket changed.
    # Keep its cash/allocation/occupancy; freeze subsequent use, never release it.
    from app.bank.service import claim_basis_current,freeze_case
    current_claims=select(BankClaim).join(BankEntry,BankEntry.id==BankClaim.entry_id).where(BankEntry.account_id.in_({a.id for a in accounts.values()}),BankClaim.state.in_(['verified_unallocated','posted','historical_bound'])).with_for_update()
    for claim in db.scalars(current_claims):
        if not claim_basis_current(db,claim):
            case=db.get(EvidenceCase,claim.case_id)
            freeze_case(db,case,'legacy_bank_basis_changed');case.version+=1
            db.add(EvidenceReview(case_id=case.id,actor_id=actor.id,action='legacy_bank_basis_changed',previous_version=case.version-1,fields={'batch_id':batch.id},reason='Historical source observation requires current identity and bank-basis review'))
    audit(db,actor,'migration.110_applied','migration_batch',batch.id,None,{'counts':batch.controls,'external_work':'paused'})
    db.flush();return {'batch_id':batch.id,'created':len(native),'state':'applied','cash_created':0,'external_work':'paused','identity_conflict_entries':conflicts}
