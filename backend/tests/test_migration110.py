import hashlib
import json
import sqlite3
import pytest
from sqlalchemy import select,func
from app.common import DomainError
from app.models import BankAccount,BankAccountWarehouse,BankEntry,BankClaim,EvidenceCase,EvidenceReview,Delivery,BankSourceRow,Payment,LegacyArchive
from app.migration.sqlite_source import consistent_snapshot,export_snapshot
from app.migration.legacy110 import native_plan,apply_native
from test_finance import actor,call,seed


def test_consistent110_native_occupied_claim_and_history_do_not_replay(app,tmp_path):
    original=b'original synthetic bank bytes';sha=hashlib.sha256(original).hexdigest()
    (tmp_path/'source').mkdir()
    source=tmp_path/'source'/'owner.db'
    with sqlite3.connect(source) as s:
        s.execute('CREATE TABLE bank_files(id INTEGER PRIMARY KEY,sha256 TEXT)');s.execute('INSERT INTO bank_files VALUES(1,?)',(sha,))
        s.execute('CREATE TABLE bank_imports(id INTEGER PRIMARY KEY,file_id INTEGER,currency TEXT,sheet TEXT)');s.execute("INSERT INTO bank_imports VALUES(1,1,'NGN','Statement')")
        s.execute('CREATE TABLE bank_rows(id INTEGER PRIMARY KEY,import_id INTEGER,source_row INTEGER,credit TEXT,debit TEXT,transaction_date TEXT,reference TEXT,description TEXT,raw_values TEXT,fingerprint TEXT)')
        s.execute("INSERT INTO bank_rows VALUES(1,1,2,'100','0','2026-01-01','REF','source','[1,100]','same-source-fingerprint')")
        s.execute("INSERT INTO bank_rows VALUES(2,1,3,'100','0','2026-01-01','REF','source','[1,100]','same-source-fingerprint')")
        s.execute('CREATE TABLE workflow_cases(id INTEGER PRIMARY KEY,scope TEXT,result_json TEXT,manual_json TEXT,source_key TEXT)')
        s.execute('INSERT INTO workflow_cases VALUES(1,?,?,?,?)',('live',json.dumps({'normalized_fields':{'amount':'100','currency':'NGN','transaction_date':'2026-01-01'}}),'{}','message:source'))
        s.execute('CREATE TABLE workflow_ledger(id INTEGER PRIMARY KEY,case_id INTEGER,bank_row_id INTEGER,state TEXT,actor TEXT)');s.execute("INSERT INTO workflow_ledger VALUES(1,1,1,'confirmed','original actor text')")
        s.execute('CREATE TABLE bank_claims(bank_row_id INTEGER PRIMARY KEY,root_id TEXT)');s.execute("INSERT INTO bank_claims VALUES(1,'old-root')")
        s.execute('CREATE TABLE workflow_reviews(id INTEGER PRIMARY KEY,case_id INTEGER,actor TEXT,action TEXT)');s.execute("INSERT INTO workflow_reviews VALUES(1,1,'old reviewer','confirm')")
        s.execute('CREATE TABLE workflow_outbox(id INTEGER PRIMARY KEY,state TEXT,body TEXT)');s.executemany('INSERT INTO workflow_outbox VALUES(?,?,?)',[(1,'pending','must not replay'),(2,'uncertain','must not replay either')])
        s.execute('CREATE TABLE unknown_new_field(id INTEGER PRIMARY KEY,extra TEXT)');s.execute("INSERT INTO unknown_new_field VALUES(1,'retained')")
    frozen={'assertion':'owner_consistent_backup','source':'synthetic owner export','files':{source.name:hashlib.sha256(source.read_bytes()).hexdigest()}}
    consistent_snapshot(source,tmp_path/'acquired'/'snapshot.db',frozen_manifest=frozen)
    export_snapshot(tmp_path/'acquired'/'snapshot.db',tmp_path/'export','synthetic','synthetic owner export')
    payload=json.loads((tmp_path/'export/source.json').read_text('utf-8'))
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);warehouse,_=seed(db,who)
        account=BankAccount(warehouse_id=warehouse,scope='live',currency='NGN',bank_name='SYNTHETIC',account_number='SYNTHETIC');db.add(account);db.flush()
        db.add(BankAccountWarehouse(account_id=account.id,warehouse_id=warehouse,source='synthetic explicit owner'))
        from app.attachments import put_file
        file=put_file(db,who,original,'synthetic.csv','text/csv','user',who.id)
        mapping={'source':'synthetic explicit owner','warehouse_id':warehouse,'scope':'live','imports':{'1':{'account_id':account.id,'expected_version':1}},'bank_files':{'1':file.id},'cases':{'1':{'account_id':account.id}}}
        plan=native_plan(payload,'synthetic110',mapping)
        result=apply_native(db,who,payload,plan)
        assert result['cash_created']==0 and apply_native(db,who,payload,plan)['created']==0
        claim=db.scalar(select(BankClaim));assert claim.state=='disputed' and claim.payment_id is None
        assert db.scalar(select(func.count()).select_from(BankSourceRow))==2
        assert db.scalar(select(func.count()).select_from(EvidenceReview))==2
        assert db.scalar(select(func.count()).select_from(Payment))==0
        assert {d.state for d in db.scalars(select(Delivery))}=={'historical','uncertain'}
        assert all(d.text=='' for d in db.scalars(select(Delivery)))
        assert db.scalar(select(LegacyArchive).where(LegacyArchive.kind=='110:unknown_new_field')).raw['extra']=='retained'
        new=EvidenceCase(warehouse_id=warehouse,scope='live',account_id=account.id,amount=100,currency='NGN',state='pending');db.add(new);db.flush()
        from app.bank.service import review_case
        for entry in db.scalars(select(BankEntry)):
            with pytest.raises(DomainError) as failure:
                call(db,who,review_case,id=new.id,expected_version=1,entry_id=entry.id,entry_version=entry.version,action='confirm_manual',reason='must not reuse original occupied row')
            assert failure.value.code in ('bank_claimed','legacy_identity_review')
        # The acquired SQLite historical claim can be explicitly revalidated and
        # linked to an independently sourced old control without a second cash fact.
        original_case=db.get(EvidenceCase,claim.case_id);original_entry=db.get(BankEntry,claim.entry_id)
        call(db,who,review_case,id=original_case.id,expected_version=original_case.version,entry_id=original_entry.id,entry_version=original_entry.version,action='resolve_manual',reason='current original evidence explicitly revalidated')
        from test_migration import sample_source
        from app.migration.source import parse_source
        from app.migration.service import apply_source,plan_source,resolve_bill
        from app.models import LegacyBill,Party
        original=sample_source();bills=json.loads(original['data']['logistics_bills']);bills[0].update(received=100,discount=0);original['data']['logistics_bills']=json.dumps(bills)
        parsed=parse_source(json.dumps(original).encode());apply_source(db,who,parsed,plan_source(parsed,'mcstar-cross-source','synthetic'))
        bill=db.scalar(select(LegacyBill));party=db.scalar(select(Party))
        resolve_bill(db,who,bill.id,{'uploaded_on':'2026-01-01','dates_source':'synthetic actual upload and original due-date evidence','expected_version':1,'warehouse_id':warehouse,'party_id':party.id,'source':'explicit same historical transaction','idempotency_key':'old-control'})
        from app.migration.bank_evidence import preview,bind
        payload={'claim_id':claim.id,'claim_version':claim.version,'case_version':original_case.version,'entry_version':original_entry.version,'account_version':account.version,'control_owner_party_id':party.id,'members':[{'bill_id':bill.id,'expected_version':bill.version,'invoice_version':1,'amount_ngn':'100','source':'explicit source identity'}],'reason':'same old source transaction','source':'synthetic dual-source owner evidence'}
        bind(db,who,payload|{'preview':preview(db,who,payload),'idempotency_key':'cross-source-proof'})
        assert bill.received==100 and claim.state=='historical_bound'
        assert db.scalar(select(func.count()).select_from(Payment))==0


def dual_occupied_export(db,app,*,account=None,reference=''):
    """Reviewer's two distinct source rows/cases; include acquired-export field maps."""
    from app.attachments import put_file
    from app.migration.source import digest
    db.info['settings']=app.state.settings;who=actor(db)
    if account is None:
        wh,party=seed(db,who)
        account=BankAccount(warehouse_id=wh,scope='live',currency='NGN',bank_name='SYNTHETIC',account_number='SYNTHETIC');db.add(account);db.flush()
        db.add(BankAccountWarehouse(account_id=account.id,warehouse_id=wh,source='synthetic owner proof'))
    else:wh=account.warehouse_id
    blob=b'synthetic independent statement rows';attachment=put_file(db,who,blob,'synthetic.csv','text/csv','user',who.id)
    raw={'bank_files':[{'id':1,'sha256':hashlib.sha256(blob).hexdigest()}],'bank_imports':[{'id':1,'file_id':1,'currency':'NGN','sheet':'Statement'}],'bank_rows':[],'workflow_cases':[],'workflow_ledger':[]}
    for i in (1,2):
        raw['bank_rows'].append({'id':i,'import_id':1,'source_row':i+1,'credit':'100','debit':'0','transaction_date':'2026-01-01','reference':reference,'description':'source','raw_values':[i,100],'fingerprint':'source-row-'+str(i)})
        raw['workflow_cases'].append({'id':i,'scope':'live','result_json':json.dumps({'normalized_fields':{'amount':'100','currency':'NGN','transaction_date':'2026-01-01'}}),'manual_json':'{}','source_key':'synthetic-case-'+str(i)})
        raw['workflow_ledger'].append({'id':i,'case_id':i,'bank_row_id':i,'state':'confirmed','actor':'original actor'})
    source={'classification':'synthetic','source':'synthetic independent two-row originals','snapshot_sha256':'a'*64,'rows':{k:[{'identity':str(r['id']),'pointer':'/'+k+'/'+str(r['id']),'raw':r} for r in values] for k,values in raw.items()},'maps':[]}
    def fields(value,pointer):
        source['maps'].append({'pointer':pointer,'target':'pending_110_source','value_hash':digest(value)})
        if isinstance(value,dict):
            for key,child in value.items():fields(child,pointer+'/'+str(key).replace('~','~0').replace('/','~1'))
        elif isinstance(value,list):
            for i,child in enumerate(value):fields(child,pointer+'/'+str(i))
    for rows in source['rows'].values():
        for row in rows:fields(row['raw'],row['pointer'])
    mapping={'source':'synthetic explicit original mappings','warehouse_id':wh,'scope':'live','imports':{'1':{'account_id':account.id,'expected_version':account.version}},'bank_files':{'1':attachment.id},'cases':{str(i):{'account_id':account.id} for i in (1,2)}}
    return who,account,source,native_plan(source,'review-dual-source',mapping)


def test_initial_110_dual_occupied_original_rows_persist_without_new_verification(app):
    from app.models import MigrationBatch,MigrationIssue,BankIdentityDecision,MigrationSourceMap
    with app.state.session_factory() as db,db.begin():
        who,account,source,plan=dual_occupied_export(db,app)
        try:
            with db.begin_nested():result=apply_native(db,who,source,plan)
            failure=None
        except DomainError as error:failure=error.code
        observed={'accepted':failure is None,'error':failure,'entries':db.scalar(select(func.count()).select_from(BankEntry)),'claims':db.scalar(select(func.count()).select_from(BankClaim)),'batches':db.scalar(select(func.count()).select_from(MigrationBatch))}
        assert observed=={'accepted':True,'error':None,'entries':2,'claims':2,'batches':1},observed
        assert result['cash_created']==0 and result['identity_conflict_entries']==2
        entries=list(db.scalars(select(BankEntry)));claims=list(db.scalars(select(BankClaim)));claim_ids={c.id for c in claims}
        assert len({e.fingerprint for e in entries})==2 and all(e.credit==100 and e.reference=='' for e in entries)
        assert all(c.state=='disputed' and c.payment_id is None and c.reviewed_basis_hash is None for c in claims)
        assert len({c.entry_id for c in claims})==len({c.case_id for c in claims})==2
        assert db.scalar(select(func.count()).select_from(Payment))==db.scalar(select(func.count()).select_from(BankIdentityDecision))==0
        originals=list(db.scalars(select(EvidenceReview).where(EvidenceReview.action=='legacy_import')))
        assert {r.fields['original_claim']['id'] for r in originals}=={1,2} and all(r.fields['original_claim']['state']=='confirmed' for r in originals)
        assert db.scalar(select(func.count()).select_from(MigrationIssue).where(MigrationIssue.code=='legacy_occupied_identity_conflict'))==2
        assert db.scalar(select(func.count()).select_from(MigrationSourceMap))==len(source['maps'])
        repeat=apply_native(db,who,source,plan);assert repeat['created']==0 and repeat['identity_conflict_entries']==2
        assert {c.id for c in db.scalars(select(BankClaim))}==claim_ids


@pytest.mark.parametrize('ruling',['same','distinct'])
def test_initial_110_dual_claim_identity_blocks_or_explicitly_revalidates(app,ruling):
    from decimal import Decimal
    from app.bank import service as bank
    from app.bank.identity import adjudicate
    from app.finance import service as finance
    from app.models import Party,BankIdentityDecision,Allocation
    with app.state.session_factory() as db,db.begin():
        who,account,source,plan=dual_occupied_export(db,app);apply_native(db,who,source,plan)
        claims=list(db.scalars(select(BankClaim).order_by(BankClaim.id)));entries=[db.get(BankEntry,c.entry_id) for c in claims]
        party=db.scalar(select(Party));payments=[];invoices=[]
        for claim,entry in zip(claims,entries):
            case=db.get(EvidenceCase,claim.case_id)
            for action in ('resolve_manual','release'):
                with pytest.raises(DomainError) as failure:
                    call(db,who,bank.review_case,id=case.id,expected_version=case.version,entry_id=entry.id,entry_version=entry.version,action=action,reason='must resolve identity before current use or release')
                assert failure.value.code=='legacy_identity_review'
            inv=call(db,who,finance.create_invoice,warehouse_id=account.warehouse_id,party_id=party.id,amount_ngn='100',uploaded_on='2026-01-01',reference='explicit separate current invoice')
            pay=call(db,who,finance.create_payment,warehouse_id=account.warehouse_id,party_id=party.id,amount='100',currency='NGN',paid_on='2026-01-01')
            invoices.append(inv);payments.append(pay)
            with pytest.raises(DomainError,match='unavailable'):
                call(db,who,finance.post_payment,id=pay['id'],expected_version=pay['version'],approval='bank',case_id=case.id,case_version=case.version,allocations=[{'invoice_id':inv['id'],'expected_version':inv['version'],'amount_ngn':'100'}])
        payload=dict(left_id=entries[0].id,right_id=entries[1].id,left_version=entries[0].version,right_version=entries[1].version,account_version=account.version,expected_version=0,decision=ruling,source='explicit original transaction identity evidence',reason='two observations require actual distinct or conflicting same identity',idempotency_key='identity-review')
        if ruling=='same':
            with pytest.raises(DomainError) as failure:adjudicate(db,who,payload)
            assert failure.value.code=='identity_occupied_conflict'
            for claim in claims:
                case=db.get(EvidenceCase,claim.case_id)
                with pytest.raises(DomainError,match='identity'):
                    call(db,who,bank.review_case,id=case.id,expected_version=case.version,action='release',reason='same old transaction conflict cannot free either original owner')
            assert all(c.state=='disputed' and c.payment_id is None for c in claims)
            assert db.scalar(select(func.count()).select_from(BankIdentityDecision))==0
            assert db.scalar(select(func.count()).select_from(Payment).where(Payment.state=='posted'))==0
        else:
            result=adjudicate(db,who,payload);assert adjudicate(db,who,payload)==result
            assert all(c.state=='disputed' for c in claims)  # Identity ruling is not bank verification.
            for claim,entry,inv,pay in zip(claims,entries,invoices,payments):
                case=db.get(EvidenceCase,claim.case_id)
                call(db,who,bank.review_case,id=case.id,expected_version=case.version,entry_id=entry.id,entry_version=entry.version,action='resolve_manual',reason='separate actual original bank evidence now revalidated')
                assert bank.claim_basis_current(db,claim)
                call(db,who,finance.post_payment,id=pay['id'],expected_version=pay['version'],approval='bank',case_id=case.id,case_version=case.version,allocations=[{'invoice_id':inv['id'],'expected_version':inv['version'],'amount_ngn':'100'}])
            assert all(c.state=='posted' for c in claims)
            assert db.scalar(select(func.sum(Allocation.amount_ngn)))==Decimal('200')
        assert db.scalar(select(func.count()).select_from(BankEntry))==2
        assert db.scalar(select(func.count()).select_from(BankClaim).where(BankClaim.state!='released'))==2


def test_later_110_occupied_source_freezes_native_cash_and_reverse_cannot_release_identity(app):
    from datetime import date
    from fastapi.testclient import TestClient
    from app.bank import service as bank
    from app.finance import service as finance
    from app.models import Allocation
    from test_evidence import import_bank
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,party=seed(db,who)
        info,entries=import_bank(db,who,wh,[['2026-01-01',100,0,'source','']]);entry=entries[0];account=db.get(BankAccount,info['id'])
        case=EvidenceCase(warehouse_id=wh,scope='live',account_id=account.id,amount=100,currency='NGN',transaction_date=date(2026,1,1));db.add(case);db.flush()
        call(db,who,bank.review_case,id=case.id,expected_version=case.version,entry_id=entry.id,entry_version=entry.version,action='confirm_manual',reason='normal original bank source')
        invoice=call(db,who,finance.create_invoice,warehouse_id=wh,party_id=party,amount_ngn='200',uploaded_on='2026-01-01',reference='native current invoice')
        pay=call(db,who,finance.create_payment,warehouse_id=wh,party_id=party,amount='100',currency='NGN',paid_on='2026-01-01')
        call(db,who,finance.post_payment,id=pay['id'],expected_version=pay['version'],approval='bank',case_id=case.id,case_version=case.version,allocations=[{'invoice_id':invoice['id'],'expected_version':invoice['version'],'amount_ngn':'40'}])
        payment=db.get(Payment,pay['id']);claim=db.scalar(select(BankClaim));assert claim.state=='posted'
        who,_,source,plan=dual_occupied_export(db,app,account=account);apply_native(db,who,source,plan)
        assert claim.state=='disputed' and payment.bank_state=='disputed' and payment.state=='posted'
        assert finance.total(db,Allocation,'amount_ngn',payment_id=payment.id)==40
        assert not bank.claim_basis_current(db,claim)
        with pytest.raises(DomainError,match='Resolve bank dispute'):
            call(db,who,finance.post_payment,id=payment.id,expected_version=payment.version,allocations=[{'invoice_id':invoice['id'],'expected_version':db.get(__import__('app.models',fromlist=['Invoice']).Invoice,invoice['id']).version,'amount_ngn':'10'}])
        payment_id=payment.id;version=payment.version;case_id=case.id
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        response=client.post('/api/payments/'+payment_id+'/reverse',json={'expected_version':version,'confirmed':True,'reason':'cannot free unresolved occupied identity via cash reversal','claim_resolution':'released'},headers={'X-CSRF-Token':login['csrf_token'],'Idempotency-Key':'unsafe-reverse-release'})
        assert response.status_code==409,response.text
    with app.state.session_factory() as db,db.begin():
        payment=db.get(Payment,payment_id);claim=db.scalar(select(BankClaim).where(BankClaim.case_id==case_id))
        assert payment.state=='posted' and claim.state=='disputed'
        assert finance.total(db,Allocation,'amount_ngn',payment_id=payment_id)==40
        assert db.scalar(select(func.count()).select_from(BankClaim).where(BankClaim.state!='released'))==3
        # Explicitly prove all three observations are distinct; identity review
        # alone does not repost/reallocate the existing actual payment.
        from itertools import combinations
        from app.bank.identity import adjudicate
        who=actor(db);entries=list(db.scalars(select(BankEntry)))
        for left,right in combinations(entries,2):
            adjudicate(db,who,dict(left_id=left.id,right_id=right.id,left_version=left.version,right_version=right.version,account_version=db.get(BankAccount,left.account_id).version,expected_version=0,decision='distinct',source='three distinct original transaction records',reason='explicit original bank identity reconciliation',idempotency_key=left.id+right.id))
        case=db.get(EvidenceCase,case_id);entry=db.get(BankEntry,claim.entry_id)
        call(db,who,bank.review_case,id=case.id,expected_version=case.version,entry_id=entry.id,entry_version=entry.version,action='resolve_manual',reason='existing actual payment retains original occupied entry')
        assert claim.state=='posted' and claim.payment_id==payment_id and payment.state=='posted' and payment.bank_state=='verified'
        assert db.scalar(select(func.count()).select_from(Payment))==1 and finance.total(db,Allocation,'amount_ngn',payment_id=payment_id)==40
        call(db,who,finance.reverse_payment,id=payment_id,expected_version=payment.version,confirmed=True,reason='explicit actual reversal after full distinct-source review',claim_resolution='released')
        assert payment.state=='reversed' and claim.state=='released' and finance.total(db,Allocation,'amount_ngn',payment_id=payment_id)==0


def test_distinct_historical_rows_allow_explicit_unposted_release_without_erasing_source(app):
    from app.bank import service as bank
    from app.bank.identity import adjudicate
    with app.state.session_factory() as db,db.begin():
        who,account,source,plan=dual_occupied_export(db,app);apply_native(db,who,source,plan)
        claims=list(db.scalars(select(BankClaim)));entries=[db.get(BankEntry,c.entry_id) for c in claims]
        adjudicate(db,who,dict(left_id=entries[0].id,right_id=entries[1].id,left_version=entries[0].version,right_version=entries[1].version,account_version=account.version,expected_version=0,decision='distinct',source='two separate bank originals',reason='explicit original transaction distinction',idempotency_key='distinct'))
        case=db.get(EvidenceCase,claims[0].case_id)
        call(db,who,bank.review_case,id=case.id,expected_version=case.version,action='release',reason='explicit withdrawal of this source case after identity resolution')
        assert claims[0].state=='released' and claims[1].state=='disputed'
        assert db.scalar(select(func.count()).select_from(BankEntry))==2 and db.scalar(select(func.count()).select_from(BankClaim))==2
        assert db.scalar(select(func.count()).select_from(EvidenceReview).where(EvidenceReview.action=='legacy_import'))==2
        assert db.scalar(select(func.count()).select_from(Payment))==0


def test_later_110_source_preserves_and_freezes_existing_historical_bank_binding(app,tmp_path):
    from app.models import LegacyBill,LegacyBankEvidence,Invoice
    from app.finance import service as finance
    from app.bank import service as bank
    from app.migration.service import historical_received
    test_consistent110_native_occupied_claim_and_history_do_not_replay(app,tmp_path)
    with app.state.session_factory() as db,db.begin():
        claim=db.scalar(select(BankClaim));case=db.get(EvidenceCase,claim.case_id);account=db.get(BankAccount,case.account_id)
        proof=db.scalar(select(LegacyBankEvidence));bill=db.scalar(select(LegacyBill));old_review_fields=[r.fields for r in db.scalars(select(EvidenceReview).where(EvidenceReview.action=='legacy_import'))]
        assert claim.state=='historical_bound' and proof.state=='linked'
        who,_,source,plan=dual_occupied_export(db,app,account=account,reference='REF');apply_native(db,who,source,plan)
        assert claim.state=='disputed' and proof.state=='disputed' and claim.payment_id is None
        assert bill.received==100 and historical_received(db,bill.native_id)==100 and finance.invoice_json(db,db.get(Invoice,bill.native_id))['historical_bank_state']=='disputed'
        assert db.scalar(select(func.count()).select_from(Payment))==0
        assert db.scalar(select(func.count()).select_from(BankClaim).where(BankClaim.state!='released'))==3
        assert all(value in [r.fields for r in db.scalars(select(EvidenceReview).where(EvidenceReview.action=='legacy_import'))] for value in old_review_fields)
        for action in ('release','resolve_manual'):
            with pytest.raises(DomainError,match='Historical evidence'):
                call(db,who,bank.review_case,id=case.id,expected_version=case.version,entry_id=claim.entry_id,entry_version=db.get(BankEntry,claim.entry_id).version,action=action,reason='identity review cannot remove historical proof protection')
