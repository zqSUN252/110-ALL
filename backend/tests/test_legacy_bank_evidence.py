import json
from datetime import date
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,func
from fastapi.testclient import TestClient
from app.models import (LegacyBill,BankAccount,BankAccountWarehouse,BankEntry,BankClaim,EvidenceCase,Invoice,Payment,Allocation,LegacyBankEvidence,LegacyBankCoverage,LegacyPaymentObservation,Grant)
from app.common import DomainError,Actor
from app.migration.service import resolve_bill
from app.migration.bank_evidence import preview,bind
from app.bank import service as bank
from app.finance import service as finance
from test_migration import applied,sample_source
from test_finance import actor,call,seed


def prepared(app,two=False):
    source=sample_source();bills=json.loads(source['data']['logistics_bills']);bills[0].update(received=40 if two else 80,discount=0)
    bills[0]['slips'][0]['approvedAt']=1700000000000
    if two:bills.append(dict(bills[0],id='b2',no='B2',slips=[]))
    source['data']['logistics_bills']=json.dumps(bills);applied(app,source)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,party=seed(db,who);members=[]
        for bill in db.scalars(select(LegacyBill)):
            res=resolve_bill(db,who,bill.id,{'expected_version':1,'warehouse_id':wh,'party_id':party,'source':'confirmed original control owner','idempotency_key':bill.id})
            members.append({'bill_id':bill.id,'expected_version':bill.version,'invoice_version':1,'amount_ngn':'40' if two else '80','source':'explicit original source relation'})
        account=BankAccount(warehouse_id=wh,scope='live',currency='NGN',bank_name='SYNTHETIC',account_number='SYNTHETIC');db.add(account);db.flush();db.add(BankAccountWarehouse(account_id=account.id,warehouse_id=wh,source='confirmed owner'))
        entry=BankEntry(account_id=account.id,currency='NGN',credit=80,debit=0,transaction_date=date(2026,1,1),reference='SYNTHETIC',description='synthetic original',fingerprint='a'*64,issues=[]);db.add(entry)
        case=EvidenceCase(account_id=account.id,warehouse_id=wh,scope='live',currency='NGN',amount=80,transaction_date=date(2026,1,1));db.add(case);db.flush()
        call(db,who,bank.review_case,id=case.id,expected_version=1,entry_id=entry.id,entry_version=1,action='confirm_manual',reason='explicit original bank evidence')
        claim=db.scalar(select(BankClaim));data={'claim_id':claim.id,'claim_version':claim.version,'case_version':case.version,'entry_version':entry.version,'account_version':account.version,'control_owner_party_id':party,'members':members,'reason':'same original payment explicitly confirmed','source':'reviewed source relation','cross_party':None}
        dates=[o.approved_at for o in db.scalars(select(LegacyPaymentObservation).order_by(LegacyPaymentObservation.id))]
    return data,dates


@pytest.mark.parametrize('two',[False,True])
def test_http_historical_bank_evidence_no_new_cash_or_dates_and_concurrent_single_claim(app,two):
    data,dates=prepared(app,two)
    clients=[]
    try:
        for _ in range(2):
            client=TestClient(app);client.__enter__();auth=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json();clients.append((client,{'X-CSRF-Token':auth['csrf_token']}))
        response=clients[0][0].post('/api/legacy/bank-evidence/preview',json=data,headers=clients[0][1]);assert response.status_code==200,response.text
        payload=data|{'preview':response.json()}
        def attempt(i):
            client,headers=clients[i]
            return client.post('/api/legacy/bank-evidence/confirm',json=payload,headers=headers|{'Idempotency-Key':'bind-'+str(i)})
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[0,1]))
        assert sorted(r.status_code for r in results)==[200,409]
        winner=next(i for i,r in enumerate(results) if r.status_code==200)
        assert attempt(winner).json()==results[winner].json()
        with app.state.session_factory() as db,db.begin():
            who=actor(db);claim=db.get(BankClaim,data['claim_id']);case=db.get(EvidenceCase,claim.case_id);entry=db.get(BankEntry,claim.entry_id)
            assert claim.state=='historical_bound' and claim.payment_id is None
            assert db.scalar(select(func.count()).select_from(Payment))==0 and db.scalar(select(func.count()).select_from(Allocation))==0
            assert db.scalar(select(func.sum(LegacyBankCoverage.amount_ngn)))==80
            assert [o.approved_at for o in db.scalars(select(LegacyPaymentObservation).order_by(LegacyPaymentObservation.id))]==dates
            for bill in db.scalars(select(LegacyBill)):
                projection=finance.invoice_json(db,db.get(Invoice,bill.native_id))
                assert projection['historical_received_ngn']==('40.00' if two else '80.00') and projection['historical_bank_state']=='linked'
            for action in ('release','resolve_manual'):
                with pytest.raises(DomainError,match='Historical evidence'):
                    call(db,who,bank.review_case,id=case.id,expected_version=case.version,entry_id=entry.id,entry_version=entry.version,action=action,reason='cannot bypass proof')
            from types import SimpleNamespace
            with pytest.raises(DomainError):finance.bind_claim(db,who,SimpleNamespace(id='not-a-payment',warehouse_id=case.warehouse_id),case.id,case.version)
            other=EvidenceCase(account_id=entry.account_id,warehouse_id=case.warehouse_id,scope='live',currency='NGN',amount=80);db.add(other);db.flush()
            with pytest.raises(DomainError,match='occupied'):call(db,who,bank.review_case,id=other.id,expected_version=1,entry_id=entry.id,entry_version=entry.version,action='confirm_manual',reason='must not reclaim')
            bank.freeze_case(db,case,'changed original evidence')
            assert db.scalar(select(LegacyBankEvidence)).state=='disputed' and claim.state=='disputed'
            for bill in db.scalars(select(LegacyBill)):
                assert finance.invoice_json(db,db.get(Invoice,bill.native_id))['historical_bank_state']=='disputed'
            # A revoked administrator cannot replay a previously successful confirmation.
            db.query(Grant).filter(Grant.user_id==who.id,Grant.capability=='*').delete()
        assert attempt(winner).status_code==403
    finally:
        for client,_ in clients:client.__exit__(None,None,None)


def test_bank_evidence_rejects_stale_scope_basis_and_uncovered_credit(app):
    data,_=prepared(app)
    with app.state.session_factory() as db,db.begin():
        who=actor(db)
        with pytest.raises(DomainError,match='Record changed'):preview(db,who,data|{'entry_version':999})
        with pytest.raises(DomainError):preview(db,Actor(who.id,{'legacy.global_read','legacy.resolve','finance.read'},set()),data)
        bad=dict(data,members=[dict(data['members'][0],amount_ngn='70')])
        with pytest.raises(DomainError,match='Exact'):preview(db,who,bad)
        invoice=db.get(Invoice,db.get(LegacyBill,data['members'][0]['bill_id']).native_id)
        invoice.scope='qa'
        with pytest.raises(DomainError,match='scope'):preview(db,who,data)
        invoice.scope='live'
        claim=db.get(BankClaim,data['claim_id']);entry=db.get(BankEntry,claim.entry_id);entry.version+=1;db.flush()
        with pytest.raises(DomainError,match='basis'):preview(db,who,data|{'entry_version':entry.version})


def test_bound_bank_evidence_survives_empty_restore_and_refuses_downgrade(app,tmp_path):
    from alembic import command
    from app.cli import migration_config
    from app.migration.backup import backup,restore
    from app.db import make_engine,session_factory
    from sqlalchemy.engine import make_url
    from sqlalchemy import text
    from dataclasses import replace
    from uuid import uuid4
    data,_=prepared(app,two=True)
    with app.state.session_factory() as db,db.begin():
        who=actor(db);bind(db,who,data|{'preview':preview(db,who,data),'idempotency_key':'restore-proof'})
    cfg=migration_config();cfg.attributes['database_url']=app.state.settings.database_url
    with pytest.raises(RuntimeError,match='historical bank evidence'):command.downgrade(cfg,'f11000500004')
    bundle=tmp_path/'proof-backup';manifest=backup(app.state.settings,bundle)
    schema='restore_'+uuid4().hex
    with app.state.engine.begin() as c:c.execute(text(f'CREATE SCHEMA {schema}'))
    target=replace(app.state.settings,database_url=make_url(app.state.settings.database_url).update_query_dict({'options':f'-csearch_path={schema}'}).render_as_string(hide_password=False),private_root=tmp_path/'restored-proof')
    try:
        assert restore(target,bundle)['verified']
        engine=make_engine(target.database_url)
        try:
            with session_factory(engine)() as db:
                assert db.scalar(select(func.count()).select_from(LegacyBankEvidence))==1
                assert db.scalar(select(func.sum(LegacyBankCoverage.amount_ngn)))==80
                assert db.scalar(select(BankClaim)).state=='historical_bound'
        finally:engine.dispose()
    finally:
        with app.state.engine.begin() as c:c.execute(text(f'DROP SCHEMA {schema} CASCADE'))


def test_cross_owner_legacy_evidence_needs_exact_extra_authority(app):
    from app.models import Party,PartyWarehouse
    data,_=prepared(app,two=True)
    with app.state.session_factory() as db,db.begin():
        who=actor(db);bill=db.get(LegacyBill,data['members'][1]['bill_id']);invoice=db.get(Invoice,bill.native_id)
        party=Party(warehouse_id=invoice.warehouse_id,name='explicit second control owner');db.add(party);db.flush();db.add(PartyWarehouse(party_id=party.id,warehouse_id=invoice.warehouse_id,source='explicit synthetic beneficiary'));invoice.party_id=party.id;db.flush()
        with pytest.raises(DomainError,match='beneficiary'):preview(db,who,data)
        data['cross_party']={'source':'source owner authorizes exact beneficiary','beneficiaries':[{'invoice_id':invoice.id,'party_id':party.id}]}
        current=preview(db,who,data)
        caps={'legacy.global_read','legacy.resolve','finance.read','finance.post','bank.read','bank.review','evidence.review'}
        with pytest.raises(DomainError):preview(db,Actor(who.id,caps,{invoice.warehouse_id}),data)
        assert preview(db,Actor(who.id,caps|{'finance.cross_party'},{invoice.warehouse_id}),data)==current
