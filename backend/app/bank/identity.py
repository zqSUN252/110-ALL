"""Source rows stay distinct; content equivalence is only an identity candidate."""
from decimal import Decimal
from sqlalchemy import select,or_
from app.models import BankEntry,BankSourceRow,BankClaim,BankIdentityDecision
from app.common import DomainError,require,check_version,audit,run_command,json_value
from app.migration.source import digest
from app.finance.service import locked
from app.finance.access import account_access


def content(entry):
    return json_value({k:getattr(entry,k) for k in ('account_id','currency','transaction_date','credit','debit','reference','description')})


def equivalent(left,right):
    return all(getattr(left,k)==getattr(right,k) for k in ('account_id','currency','transaction_date','credit','debit')) and all(str(getattr(left,k) or '').strip().casefold()==str(getattr(right,k) or '').strip().casefold() for k in ('reference','description'))


def legacy(db,entry):
    return db.scalar(select(BankSourceRow.id).where(BankSourceRow.entry_id==entry.id,BankSourceRow.parsed['legacy_batch'].astext.is_not(None)).limit(1)) is not None


def candidates(db,entry):
    rows=db.scalars(select(BankEntry).where(BankEntry.account_id==entry.account_id,BankEntry.currency==entry.currency,BankEntry.credit==entry.credit,BankEntry.debit==entry.debit,BankEntry.id!=entry.id))
    return [row for row in rows if equivalent(entry,row)]


def decision(db,left,right):
    a,b=sorted((left.id,right.id))
    row=db.scalar(select(BankIdentityDecision).where(BankIdentityDecision.left_id==a,BankIdentityDecision.right_id==b))
    if row and row.basis=={a:digest(content(db.get(BankEntry,a))),b:digest(content(db.get(BankEntry,b)))}:return row.decision
    return None


def guard_claim(db,entry):
    """The global financial command guard serializes all claim/adjudication writes."""
    seen={entry.id};pending=[entry]
    while pending:
        current=pending.pop()
        for other in candidates(db,current):
            if not (legacy(db,current) or legacy(db,other)):continue
            ruling=decision(db,current,other)
            if ruling=='distinct':continue
            occupied=db.scalar(select(BankClaim.id).where(BankClaim.entry_id==other.id,BankClaim.state!='released'))
            if other.id!=entry.id and occupied:
                raise DomainError('legacy_identity_review','Possible same occupied source transaction requires explicit identity adjudication',409)
            if ruling=='same' and other.id not in seen:seen.add(other.id);pending.append(other)


def adjudicate(db,actor,p):
    from app.models import BankAccount
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    left=locked(db,BankEntry,p['left_id']);right=locked(db,BankEntry,p['right_id'])
    if left.id==right.id or left.account_id!=right.account_id or not equivalent(left,right):raise DomainError('identity_pair','Choose two explicit current candidate rows')
    if not (legacy(db,left) or legacy(db,right)):raise DomainError('legacy_identity_pair','This adjudication bridges an original legacy source observation')
    account=locked(db,BankAccount,left.account_id)
    account_access(db,actor,account,'bank.identity');account_access(db,actor,account,'bank.override')
    from app.models import EvidenceCase,Payment,LegacyBankCoverage,LegacyBill,Invoice
    from app.finance.access import payment_access
    for claim in db.scalars(select(BankClaim).where(BankClaim.entry_id.in_((left.id,right.id)),BankClaim.state!='released')):
        case=locked(db,EvidenceCase,claim.case_id)
        for cap in ('bank.identity','bank.override','evidence.read'):require(actor,cap,case.warehouse_id)
        if claim.payment_id:payment_access(db,actor,locked(db,Payment,claim.payment_id),'finance.read')
        from app.models import LegacyBankEvidence
        for inv in db.scalars(select(Invoice).join(LegacyBill,LegacyBill.native_id==Invoice.id).join(LegacyBankCoverage,LegacyBankCoverage.bill_id==LegacyBill.id).join(LegacyBankEvidence,LegacyBankEvidence.id==LegacyBankCoverage.evidence_id).where(LegacyBankEvidence.claim_id==claim.id)):
            require(actor,'finance.read',inv.warehouse_id);require(actor,'bank.identity',inv.warehouse_id)
    def apply():
        check_version(left,p.get('left_version'));check_version(right,p.get('right_version'));check_version(account,p.get('account_version'))
        if p.get('decision') not in ('same','distinct') or not str(p.get('source','')).strip() or not str(p.get('reason','')).strip():raise DomainError('identity_source','Explicit same/distinct ruling with original evidence and reason required')
        a,b=sorted((left.id,right.id));row=db.scalar(select(BankIdentityDecision).where(BankIdentityDecision.left_id==a,BankIdentityDecision.right_id==b))
        claims=list(db.scalars(select(BankClaim).where(BankClaim.entry_id.in_((a,b)),BankClaim.state!='released')))
        if p['decision']=='same' and len(claims)>1:raise DomainError('identity_occupied_conflict','Both observations already occupied: reconcile facts without releasing either',409)
        if row:check_version(row,p.get('expected_version'));row.version+=1
        else:
            if p.get('expected_version',0)!=0:raise DomainError('stale_version','Identity ruling changed',409)
            row=BankIdentityDecision(left_id=a,right_id=b);db.add(row)
        row.decision=p['decision'];row.basis={a:digest(content(db.get(BankEntry,a))),b:digest(content(db.get(BankEntry,b)))};row.actor_id=actor.id;row.source=p['source'];row.reason=p['reason']
        audit(db,actor,'bank.identity_adjudicated','bank_entry',left.id,None,{'other_id':right.id,'decision':p['decision'],'source':p['source']})
        db.flush();return {'id':row.id,'version':row.version,'decision':row.decision,'source_rows_preserved':True}
    return run_command(db,actor,'bank.identity.adjudicate',p.get('idempotency_key'),p,apply)
