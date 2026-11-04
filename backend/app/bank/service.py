from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from sqlalchemy import select,func,or_,and_
from app.common import DomainError, require, check_version, json_value
from app.models import (Attachment, BankAccount, BankFile, BankSourceRow, BankEntry, BankClaim,
    BankMatch, EvidenceCase, EvidenceImage, EvidenceReview, Payment, FinanceEvent,BankAccountWarehouse,Warehouse)
from app.attachments import read_file
from app.finance.service import command, locked, access, record, scope, decimal, day
from app.finance.access import account_access
from . import parser, matching


@command('bank.account.create','warehouse_id scope bank_name account_number currency')
def create_account(db,actor,p):
    require(actor,'bank.manage',p['warehouse_id']); scope(p.get('scope','live'))
    if p['currency'] not in ('NGN','USD','CNY'):
        raise DomainError('invalid_currency','Unsupported bank currency')
    values={k:v for k,v in p.items() if k!='idempotency_key'}
    values['bank_name']=matching.bank_name(values['bank_name'])
    values['account_number']=''.join(values['account_number'].split())
    if not values['bank_name'] or not values['account_number']:raise DomainError('account_identity_required','Explicit bank and account identity required')
    account=BankAccount(**values)
    db.add(account); db.flush()
    db.add(BankAccountWarehouse(account_id=account.id,warehouse_id=account.warehouse_id,source='Explicit home warehouse on account creation'))
    db.flush();return record(account)


@command('bank.account.membership','id expected_version warehouse_id active source')
def set_account_membership(db,actor,p):
    account=access(db,actor,BankAccount,p['id'],'bank.manage');check_version(account,p.get('expected_version'))
    require(actor,'bank.manage',p['warehouse_id']);locked(db,Warehouse,p['warehouse_id'])
    if not isinstance(p.get('active'),bool) or not isinstance(p.get('source'),str) or not p['source'].strip():raise DomainError('membership_evidence','Explicit account participation and evidence required')
    member=db.scalar(select(BankAccountWarehouse).where(BankAccountWarehouse.account_id==account.id,BankAccountWarehouse.warehouse_id==p['warehouse_id']).with_for_update())
    if member:member.active=p['active'];member.source=p['source'];member.version+=1
    else:db.add(BankAccountWarehouse(account_id=account.id,warehouse_id=p['warehouse_id'],active=p['active'],source=p['source']))
    account.version+=1;db.flush()
    return record(account)|{'memberships':[record(m) for m in db.scalars(select(BankAccountWarehouse).where(BankAccountWarehouse.account_id==account.id))]}


def attachment_for_actor(db,actor,identity):
    attachment=locked(db,Attachment,identity)
    if '*' not in actor.capabilities and not (attachment.owner_type=='user' and attachment.owner_id==actor.id):
        raise DomainError('forbidden','Use a personally uploaded original attachment',403)
    return attachment


def parsed_file(db,attachment):
    cached=db.info.get('bank_preparsed',{}).get(attachment.id)
    if cached and cached[0]==attachment.sha256:return cached[1]
    content=read_file(db.info['settings'],attachment)
    return parse_bytes(db.info['settings'],content)


def parse_bytes(settings,content):
    with TemporaryDirectory(dir=settings.private_root) as directory:
        path=Path(directory)/('original.xls' if content[:8]==bytes.fromhex('d0cf11e0a1b11ae1') else 'original.xlsx')
        path.write_bytes(content)
        try:
            parsed=parser.read_workbook(path)
            raw=[(name, rows) for name,rows,_ in parser.workbook_rows(path)]
        except parser.BankFileError as exc:
            raise DomainError('bank_parse_error',str(exc)) from None
    return parsed,raw


@command('bank.import.preview','account_id attachment_id')
def import_preview(db,actor,p):
    account=access(db,actor,BankAccount,p['account_id'],'bank.import')
    attachment=attachment_for_actor(db,actor,p['attachment_id'])
    previous=db.scalar(select(BankFile).where(BankFile.account_id==account.id,BankFile.sha256==attachment.sha256))
    if previous: return get_import(db,actor,previous.id)
    parsed,raw=parsed_file(db,attachment)
    file=BankFile(account_id=account.id,attachment_id=attachment.id,sha256=attachment.sha256)
    db.add(file); db.flush()
    index={(s['name'],r['row']):r for s in parsed['sheets'] for r in s['rows']}
    # Store all source rows, including rejected/zero/header rows; never lose provenance.
    for sheet,rows in raw:
        for n,values in enumerate(rows,1):
            db.add(BankSourceRow(file_id=file.id,sheet=sheet,row_number=n,raw_values=[parser.text(v) for v in values],parsed=index.get((sheet,n))))
    file.mapping={'preview':[parser.sheet_preview(s) for s in parsed['sheets']]}
    db.flush(); return get_import(db,actor,file.id)


def get_import(db,actor,identity):
    file=locked(db,BankFile,identity); account=access(db,actor,BankAccount,file.account_id,'bank.read')
    return record(file)|{'scope':account.scope,'coverage':'unknown','row_count':db.scalar(select(func.count()).select_from(BankSourceRow).where(BankSourceRow.file_id==file.id))}


@command('bank.import.confirm','id expected_version account_version sheets date_order trusted_reference')
def confirm_import(db,actor,p):
    file=locked(db,BankFile,p['id']); account=access(db,actor,BankAccount,file.account_id,'bank.import')
    check_version(file,p.get('expected_version')); check_version(account,p.get('account_version'))
    if file.state!='preview': raise DomainError('already_confirmed','Import already confirmed',409)
    sheets=p['sheets']; order=p.get('date_order','auto')
    if not isinstance(sheets,list) or not sheets or order not in ('auto','dmy','mdy'):
        raise DomainError('invalid_mapping','Select sheets and explicit date order')
    known={s['name'] for s in file.mapping['preview']}
    if set(sheets)-known: raise DomainError('invalid_sheet','Unknown worksheet')
    trusted=p.get('trusted_reference',False)
    if type(trusted) is not bool: raise DomainError('invalid_mapping','trusted_reference must be boolean')
    if trusted: account_access(db,actor,account,'bank.identity')
    count=0; conflicts=0; changed_buckets=set()
    for row in db.scalars(select(BankSourceRow).where(BankSourceRow.file_id==file.id,BankSourceRow.sheet.in_(sheets)).order_by(BankSourceRow.sheet,BankSourceRow.row_number)):
        if not row.parsed: continue
        r=row.parsed; resolved,error=parser.date_value(r['raw_date'],order)
        issues=[v for v in r['issues'] if v not in ('缺少日期','日月顺序不明确，请选择日期格式','日期无法识别')]
        if error: issues.append(error)
        identity=[account.id,account.currency,resolved or r['raw_date'],r['credit'],r['debit'],r['description'],r['reference']]
        fingerprint=hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()
        trusted_id=r['reference'] if trusted and r['reference'] else None
        existing=db.scalar(select(BankEntry).where(BankEntry.account_id==account.id,BankEntry.trusted_id==trusted_id)) if trusted_id else None
        if existing:
            if existing.fingerprint!=fingerprint:
                raise DomainError('bank_identity_conflict','Trusted transaction ID has conflicting original values',409,{'source_row_id':row.id})
            row.entry_id=existing.id
        else:
            duplicates=list(db.scalars(select(BankEntry).where(BankEntry.account_id==account.id,BankEntry.fingerprint==fingerprint).with_for_update()))
            from .identity import equivalent,legacy
            observed=BankEntry(account_id=account.id,currency=account.currency,credit=Decimal(r['credit']),debit=Decimal(r['debit']),transaction_date=day(resolved) if resolved else None,reference=r['reference'],description=r['description'])
            for candidate in db.scalars(select(BankEntry).where(BankEntry.account_id==account.id,BankEntry.currency==account.currency,BankEntry.credit==observed.credit,BankEntry.debit==observed.debit)):
                if candidate not in duplicates and legacy(db,candidate) and equivalent(candidate,observed):duplicates.append(candidate)
            if duplicates:
                issues.append('possible_cross_file_duplicate'); conflicts+=1
                for duplicate in duplicates:
                    duplicate.issues=sorted(set(duplicate.issues)|{'possible_cross_file_duplicate'})
                    duplicate.version+=1
            entry=BankEntry(account_id=account.id,currency=account.currency,credit=Decimal(r['credit']),debit=Decimal(r['debit']),transaction_date=day(resolved) if resolved else None,
                trusted_id=trusted_id,reference=r['reference'],description=r['description'],fingerprint=fingerprint,issues=issues)
            db.add(entry); db.flush(); row.entry_id=entry.id
            if entry.credit>0 and entry.debit==0:changed_buckets.add((entry.currency,entry.credit))
        count+=1
    if not count: raise DomainError('no_transactions','Selected worksheets contain no parsed transactions')
    file.mapping={**file.mapping,'sheets':sheets,'date_order':order,'trusted_reference':trusted}
    file.state='confirmed'; file.version+=1; account.revision+=1; account.version+=1
    db.flush()
    # Revalidate only existing claims for this changed account. No model calls,
    # no cash reversal and no occupancy release. Known source observations do not
    # change the typed bank-entry basis and therefore leave the owner intact.
    affected=[]
    for currency,amount in changed_buckets:
        receipt_values=[amount,amount+Decimal('53.75')] if currency=='NGN' else [amount]
        affected.append(or_(and_(BankEntry.currency==currency,BankEntry.credit==amount),and_(EvidenceCase.currency==currency,EvidenceCase.amount.in_(receipt_values))))
    claims=select(BankClaim).join(BankEntry,BankEntry.id==BankClaim.entry_id).join(EvidenceCase,EvidenceCase.id==BankClaim.case_id).where(BankEntry.account_id==account.id,BankClaim.state.in_(['verified_unallocated','posted','historical_bound']),or_(*affected)).with_for_update() if affected else None
    for claim in db.scalars(claims) if claims is not None else []:
        case=locked(db,EvidenceCase,claim.case_id)
        if not claim_basis_current(db,claim):
            freeze_case(db,case,'bank_basis_changed');case.version+=1
            db.add(EvidenceReview(case_id=case.id,actor_id=actor.id,action='bank_basis_changed',previous_version=case.version-1,fields={'bank_revision':account.revision,'import_id':file.id},reason='New bank evidence requires revalidation'))
    return record(file)|{'transaction_rows':count,'duplicate_candidates':conflicts,'account_version':account.version,'coverage':'unknown'}


def candidate_rows(db,case,account):
    if case.amount is None: return []
    # Indexed amount buckets; retain missing dates and explicit 53.75 review candidates.
    amounts=[case.amount]
    if case.currency=='NGN': amounts.append(case.amount-Decimal('53.75'))
    entries=list(db.scalars(select(BankEntry).where(BankEntry.account_id==account.id,BankEntry.currency==case.currency,BankEntry.credit.in_(amounts))))
    result=[]
    for e in entries:
        row=db.scalar(select(BankSourceRow).where(BankSourceRow.entry_id==e.id).order_by(BankSourceRow.id).limit(1))
        file=db.get(BankFile,row.file_id)
        result.append({'id':e.id,'currency':e.currency,'credit':str(e.credit),'debit':str(e.debit),'date':e.transaction_date.isoformat() if e.transaction_date else None,
            'bank':account.bank_name,'account_suffix':account.account_number[-4:],'description':e.description,'reference':e.reference,'issues':e.issues,
            'fingerprint':e.fingerprint,'file_sha256':file.sha256,'sheet':row.sheet,'row':row.row_number,'source_code':row.parsed.get('source_code','')})
    return result


def decision(db,case):
    if not case.account_id: return {'state':'review','reason':'bank_coverage_unknown','coverage':'unknown','candidates':[],'selected':None}
    account=locked(db,BankAccount,case.account_id)
    fields={k:getattr(case,k).isoformat() if isinstance(getattr(case,k),date) else str(getattr(case,k)) if getattr(case,k) is not None else None for k in ('amount','currency','transaction_date','payer_name','transaction_reference','recipient_account','bank_name','business_reference')}
    image=db.scalar(select(EvidenceImage).where(EvidenceImage.case_id==case.id).order_by(EvidenceImage.id).limit(1))
    sha=db.get(Attachment,image.attachment_id).sha256 if image else 'manual-case:'+case.id
    result=matching.compare({'fields':fields,'sha256':sha},candidate_rows(db,case,account))
    result['coverage']='unknown'
    if result['reason']=='no_bank_amount_candidate': result['reason']='bank_coverage_unknown'
    for candidate in result['candidates']:
        claim=db.scalar(select(BankClaim).where(BankClaim.entry_id==candidate['bank']['id'],BankClaim.state!='released'))
        if claim and claim.case_id!=case.id:
            candidate.update(eligible=False,reason='bank_already_claimed')
    valid=[c for c in result['candidates'] if c['eligible']]
    if result['selected'] and not any(c['bank']['id']==result['selected']['id'] for c in valid):
        result.update(state='review',reason='bank_already_claimed',selected=None)
    return result


def basis_hash(db,case,entry_id):
    """The selected row plus complete relevant amount buckets, independent of new
    observations of an already-known trusted entry or unrelated bank revisions."""
    entries=select(BankEntry).where(BankEntry.account_id==case.account_id)
    selected=db.get(BankEntry,entry_id)
    amounts=[case.amount,selected.credit]
    if case.amount is not None and case.currency=='NGN':amounts.append(case.amount-Decimal('53.75'))
    entries=entries.where((BankEntry.id==entry_id)|((BankEntry.currency==case.currency)&BankEntry.credit.in_(amounts)&(BankEntry.credit>0)&(BankEntry.debit==0)))
    rows=[{key:json_value(getattr(e,key)) for key in ('id','version','currency','credit','debit','transaction_date','trusted_id','reference','description','issues')} for e in db.scalars(entries.order_by(BankEntry.id))]
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def record_basis(db,claim,case,entry,account):
    claim.reviewed_entry_version=entry.version;claim.reviewed_bank_revision=account.revision
    claim.reviewed_basis_hash=basis_hash(db,case,entry.id)


def claim_basis_current(db,claim):
    entry=db.get(BankEntry,claim.entry_id);case=db.get(EvidenceCase,claim.case_id)
    from .identity import guard_claim
    try:guard_claim(db,entry)
    except DomainError:return False
    return bool(case.account_id==entry.account_id and claim.reviewed_entry_version==entry.version
        and claim.reviewed_basis_hash and claim.reviewed_basis_hash==basis_hash(db,case,entry.id))


def ensure_claim_current(db,claim):
    if claim.state not in ('verified_unallocated','posted') or not claim_basis_current(db,claim):
        raise DomainError('bank_basis_changed','Bank verification is disputed or its reviewed basis changed; revalidate before posting or archiving',409)


@command('bank.case.match','id expected_version')
def match_case(db,actor,p):
    case=access(db,actor,EvidenceCase,p['id'],'bank.review'); check_version(case,p.get('expected_version'))
    result=decision(db,case)
    account=db.get(BankAccount,case.account_id) if case.account_id else None
    db.add(BankMatch(case_id=case.id,case_version=case.version,bank_revision=account.revision if account else 0,decision=result))
    return {'id':case.id,'version':case.version,**result}


@command('bank.case.review','id expected_version entry_id entry_version action reason')
def review_case(db,actor,p):
    case=access(db,actor,EvidenceCase,p['id'],'bank.review'); check_version(case,p.get('expected_version'))
    action=p.get('action','confirm')
    if action not in ('confirm','confirm_manual','defer','retract','release','resolve','resolve_manual'): raise DomainError('invalid_action','Unsupported bank review action')
    if action in ('release','resolve','resolve_manual'):
        require(actor,'bank.release' if action=='release' else 'bank.override',case.warehouse_id)
        if not isinstance(p.get('reason'),str) or not p['reason'].strip():raise DomainError('reason_required','Claim release/resolution requires original evidence and reason')
        claim=db.scalar(select(BankClaim).where(BankClaim.case_id==case.id,BankClaim.state!='released').with_for_update())
        if not claim:raise DomainError('claim_unavailable','No active claim',409)
        from app.models import LegacyBankEvidence
        if db.scalar(select(LegacyBankEvidence.id).where(LegacyBankEvidence.claim_id==claim.id)):
            raise DomainError('historical_evidence_bound','Historical evidence needs source reconciliation; generic release/resolve cannot free it',409)
        entry=locked(db,BankEntry,claim.entry_id)
        from .identity import guard_claim
        guard_claim(db,entry)
        if action=='release':
            if claim.payment_id:raise DomainError('financial_reversal_required','Posted claim requires separate explicit payment reversal',409)
            claim.state='released'; case.state='pending'
        else:
            account=locked(db,BankAccount,entry.account_id)
            account_access(db,actor,account,'bank.review',case.warehouse_id)
            if case.account_id!=account.id or case.scope!=account.scope or entry.currency!=case.currency or entry.credit<=0 or entry.debit!=0:
                raise DomainError('bank_scope','Original bank direction/currency/account scope conflicts')
            if action=='resolve_manual':
                if p.get('entry_id')!=entry.id:raise DomainError('original_entry_required','Manual revalidation must explicitly select the occupied original entry')
                check_version(entry,p.get('entry_version'))
            else:
                result=decision(db,case)
                if result.get('state')!='matched' or result['selected']['id']!=claim.entry_id:
                    raise DomainError('bank_review_required','Corrected case must uniquely match its original occupied entry',409,result)
            if claim.payment_id:
                payment=locked(db,Payment,claim.payment_id)
                if payment.state!='posted':raise DomainError('financial_review_required','Reversed payment cannot be restored through evidence',409)
                if payment.currency!=entry.currency or payment.amount!=entry.credit:raise DomainError('cash_basis_changed','Changed original cash needs separate financial review',409)
                payment.bank_state='verified'; payment.version+=1
            claim.state='posted' if claim.payment_id else 'verified_unallocated';case.state='bank_verified'
            record_basis(db,claim,case,entry,account)
        claim.version+=1;case.version+=1
        db.add(EvidenceReview(case_id=case.id,actor_id=actor.id,action=action,previous_version=case.version-1,fields={'claim_id':claim.id},reason=p['reason']))
        return {'id':case.id,'version':case.version,'claim':record(claim),'payment_posted':False}
    if action in ('defer','retract'):
        freeze_case(db,case,action); case.version+=1
        return record(case)
    entry=locked(db,BankEntry,p['entry_id']); check_version(entry,p.get('entry_version'))
    from .identity import guard_claim
    guard_claim(db,entry)
    account=access(db,actor,BankAccount,entry.account_id,'bank.review')
    account_access(db,actor,account,'bank.review',case.warehouse_id)
    if account.scope!=case.scope or case.account_id!=account.id or entry.currency!=case.currency or entry.credit<=0 or entry.debit!=0:
        raise DomainError('bank_scope','Bank account, direction or scope conflict')
    if action=='confirm':
        result=decision(db,case)
        if result.get('state')!='matched' or result['selected']['id']!=entry.id:
            raise DomainError('bank_review_required','Deterministic evidence is not uniquely sufficient',409,result)
    else:
        require(actor,'bank.override',case.warehouse_id)
        if not str(p.get('reason','')).strip(): raise DomainError('reason_required','Manual bank confirmation requires original evidence/reason')
    current=db.scalar(select(BankClaim).where(BankClaim.entry_id==entry.id,BankClaim.state!='released').with_for_update())
    if current: raise DomainError('bank_claimed','Bank entry already occupied',409)
    if db.scalar(select(BankClaim.id).where(BankClaim.case_id==case.id,BankClaim.state!='released')):
        raise DomainError('case_claimed','Case already has an active bank claim',409)
    claim=BankClaim(entry_id=entry.id,case_id=case.id,state='verified_unallocated',reason=p.get('reason') or 'deterministic-exact-v2',actor_id=actor.id)
    record_basis(db,claim,case,entry,account)
    db.add(claim); case.state='bank_verified'; case.version+=1
    db.add(EvidenceReview(case_id=case.id,actor_id=actor.id,action=action,previous_version=case.version-1,fields={'entry_id':entry.id},reason=p.get('reason')))
    db.flush(); return {'id':case.id,'version':case.version,'claim':record(claim),'payment_posted':False}


def freeze_case(db,case,reason):
    claim=db.scalar(select(BankClaim).where(BankClaim.case_id==case.id,BankClaim.state!='released').with_for_update())
    if claim:
        claim.state='disputed'; claim.version+=1
        from app.migration.bank_evidence import freeze
        freeze(db,claim)
        if claim.payment_id:
            payment=locked(db,Payment,claim.payment_id); payment.bank_state='disputed'; payment.version+=1
            db.add(FinanceEvent(payment_id=payment.id,kind='evidence_disputed'))
    case.state='disputed' if claim else reason
