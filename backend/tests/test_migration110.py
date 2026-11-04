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
