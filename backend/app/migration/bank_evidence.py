"""Explicitly supplement historical controls without creating cash or authorization."""
from decimal import Decimal
from sqlalchemy import select,func,text
from app.common import require,check_version,DomainError,json_value,run_command,audit
from app.models import (LegacyBankEvidence,LegacyBankCoverage,LegacyBill,BankClaim,BankEntry,BankAccount,EvidenceCase,Invoice)
from app.finance.service import locked,decimal,record,invalidate
from app.finance.access import account_access,party_member
from app.bank.service import claim_basis_current,ensure_claim_current


def check_access(db,actor,p):
    require(actor,'legacy.global_read');require(actor,'legacy.resolve');require(actor,'finance.read')
    claim=locked(db,BankClaim,p['claim_id']);case=locked(db,EvidenceCase,claim.case_id);entry=locked(db,BankEntry,claim.entry_id);account=locked(db,BankAccount,entry.account_id)
    for cap in ('finance.read','finance.post','bank.read','bank.review','evidence.review'):require(actor,cap,case.warehouse_id)
    account_access(db,actor,account,'bank.review',case.warehouse_id)
    from app.finance.authorization import source_assessment_access
    source_assessment_access(db,actor,case)
    if not isinstance(p.get('members'),list) or not 1<=len(p['members'])<=200 or len({m['bill_id'] for m in p['members']})!=len(p['members']):raise DomainError('coverage_members','Select distinct historical bills')
    rows=[]
    for member in sorted(p['members'],key=lambda m:m['bill_id']):
        bill=locked(db,LegacyBill,member['bill_id'])
        if not bill.native_id:raise DomainError('legacy_owner_required','Confirm native historical bill ownership first',409)
        inv=locked(db,Invoice,bill.native_id)
        for cap in ('finance.read','finance.post','bank.review'):require(actor,cap,inv.warehouse_id)
        party_member(db,inv.party_id,inv.warehouse_id);account_access(db,actor,account,'bank.review',inv.warehouse_id)
        rows.append((member,bill,inv))
    owner=p['control_owner_party_id'];different={inv.id:inv.party_id for _,_,inv in rows if inv.party_id!=owner}
    if owner not in {inv.party_id for _,_,inv in rows}:raise DomainError('legacy_owner_required','Control owner must be an explicitly selected native bill owner')
    if different:
        extra=p.get('cross_party')
        if not isinstance(extra,dict) or set(extra)!={'source','beneficiaries'} or not str(extra['source']).strip() or not isinstance(extra['beneficiaries'],list) or any(set(r)!={'invoice_id','party_id'} for r in extra['beneficiaries']) or len(extra['beneficiaries'])!=len(different) or {r['invoice_id']:r['party_id'] for r in extra['beneficiaries']}!=different:raise DomainError('cross_party_confirmation','Explicit exact source-backed beneficiary authorization required')
        for wh in {case.warehouse_id}|{inv.warehouse_id for _,_,inv in rows}:require(actor,'finance.cross_party',wh)
    elif p.get('cross_party') is not None:raise DomainError('cross_party_confirmation','No different control owner needs authorization')
    return claim,case,entry,account,rows


def detail(db,actor,identity):
    row=locked(db,LegacyBankEvidence,identity)
    members=list(db.scalars(select(LegacyBankCoverage).where(LegacyBankCoverage.evidence_id==row.id)))
    check_access(db,actor,{'claim_id':row.claim_id,'control_owner_party_id':row.control_owner_party_id,'members':[{'bill_id':m.bill_id} for m in members],'cross_party':row.cross_party})
    claim=db.get(BankClaim,row.claim_id)
    current=row.state=='linked' and claim.state=='historical_bound' and claim_basis_current(db,claim)
    return record(row)|{'current_state':'linked' if current else 'disputed','members':[record(m) for m in members],'new_cash_created':False}


def preview(db,actor,p):
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    claim,case,entry,account,rows=check_access(db,actor,p)
    check_version(claim,p['claim_version']);check_version(case,p['case_version']);check_version(entry,p['entry_version']);check_version(account,p['account_version'])
    if claim.state!='verified_unallocated' or claim.payment_id or case.payment_id or db.scalar(select(LegacyBankEvidence.id).where(LegacyBankEvidence.claim_id==claim.id)):
        raise DomainError('claim_unavailable','Select an unbound current bank verification, with no native Payment',409)
    ensure_claim_current(db,claim)
    if entry.currency!='NGN' or entry.credit<=0 or entry.debit!=0 or account.scope!=case.scope:raise DomainError('legacy_bank_scope','Explicit NGN income in the same scope required; no inferred FX')
    values=[];total=Decimal('0.00')
    for member,bill,inv in rows:
        check_version(bill,member['expected_version']);check_version(inv,member['invoice_version'])
        if inv.kind!='AR' or inv.scope!=case.scope or bill.currency!='NGN' or bill.state!='resolved':raise DomainError('legacy_bank_scope','Bill kind/currency/scope/source state conflicts')
        amount=decimal(member['amount_ngn'],positive=True)
        used=db.scalar(select(func.coalesce(func.sum(LegacyBankCoverage.amount_ngn),0)).where(LegacyBankCoverage.bill_id==bill.id))
        if used+amount>bill.received:raise DomainError('legacy_coverage_exceeds','Coverage exceeds original received; disputed evidence remains occupied',409)
        if not str(member.get('source','')).strip():raise DomainError('source_required','Explicit bill/source mapping evidence required')
        total+=amount;values.append({'bill_id':bill.id,'source_version':bill.version,'invoice_id':inv.id,'invoice_version':inv.version,'amount_ngn':amount,'historical_received_ngn':bill.received,'previous_coverage_ngn':used,'source':member['source']})
    if total!=entry.credit:raise DomainError('legacy_coverage_total','Exact selected bank credit must be covered; no inferred cash remainder')
    return json_value({'claim_id':claim.id,'claim_version':claim.version,'case_id':case.id,'case_version':case.version,'entry_id':entry.id,'entry_version':entry.version,'account_id':account.id,'account_version':account.version,'account_revision':account.revision,'basis_hash':claim.reviewed_basis_hash,'scope':case.scope,'control_owner_party_id':p['control_owner_party_id'],'members':values,'bank_credit_ngn':entry.credit,'new_cash_ngn':'0.00','cross_party':p.get('cross_party')})


def bind(db,actor,p):
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    # Replays still require all current account, owner and warehouse privileges.
    check_access(db,actor,p)
    def apply():
        current=preview(db,actor,p)
        if p.get('preview')!=current:raise DomainError('preview_changed','Review current bank/source coverage',409,current)
        if not str(p.get('reason','')).strip() or not str(p.get('source','')).strip():raise DomainError('source_required','Explicit reason and identity source are required')
        row=LegacyBankEvidence(claim_id=p['claim_id'],account_id=current['account_id'],control_owner_party_id=p['control_owner_party_id'],actor_id=actor.id,scope=current['scope'],reason=p['reason'],source=p['source'],basis=current,cross_party=p.get('cross_party'))
        db.add(row);db.flush()
        for member in current['members']:
            db.add(LegacyBankCoverage(evidence_id=row.id,bill_id=member['bill_id'],source_version=member['source_version'],amount_ngn=Decimal(member['amount_ngn']),source=member['source']))
            invalidate(db,invoice_id=member['invoice_id'],kind='historical_bank_evidence_changed')
        claim=db.get(BankClaim,p['claim_id']);claim.state='historical_bound';claim.version+=1
        case=db.get(EvidenceCase,claim.case_id);case.state='historical_bound';case.version+=1
        audit(db,actor,'legacy.bank_evidence_bound','legacy_bank_evidence',row.id,None,{'claim_id':claim.id,'bill_ids':[m['bill_id'] for m in current['members']],'cash_created':False})
        db.flush();return {'id':row.id,'version':row.version,'state':row.state,'claim_version':claim.version,'cash_created':False,'allocation_created':False}
    return run_command(db,actor,'legacy.bank_evidence',p.get('idempotency_key'),p,apply)


def freeze(db,claim):
    row=db.scalar(select(LegacyBankEvidence).where(LegacyBankEvidence.claim_id==claim.id))
    if row:
        row.state='disputed';row.version+=1
        for bill in db.scalars(select(LegacyBill).join(LegacyBankCoverage,LegacyBankCoverage.bill_id==LegacyBill.id).where(LegacyBankCoverage.evidence_id==row.id)):
            if bill.native_id:invalidate(db,invoice_id=bill.native_id,kind='historical_bank_evidence_disputed')


def bank_state(db,invoice_id,historical):
    invoice=db.get(Invoice,invoice_id)
    if not invoice or not (invoice.source_key or '').startswith('legacy:'):return None
    rows=list(db.execute(select(LegacyBankEvidence,LegacyBankCoverage).join(LegacyBankCoverage,LegacyBankCoverage.evidence_id==LegacyBankEvidence.id).join(LegacyBill,LegacyBill.id==LegacyBankCoverage.bill_id).where(LegacyBill.native_id==invoice_id)))
    if not rows:return 'unknown' if historical else None
    if any(h.state!='linked' or (claim:=db.get(BankClaim,h.claim_id)).state!='historical_bound' or not claim_basis_current(db,claim) for h,m in rows):return 'disputed'
    return 'linked' if sum((m.amount_ngn for h,m in rows),Decimal(0))==historical else 'partially_linked'
