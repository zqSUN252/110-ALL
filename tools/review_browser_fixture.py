"""Typed synthetic picker population, strictly limited to an existing test_ui schema."""
import json,os,sys
from pathlib import Path
from uuid import uuid4
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from sqlalchemy import select
from sqlalchemy.engine import make_url
from app.settings import load_settings
from app.db import make_engine,session_factory
from app.common import Actor
from app.models import User,BankEntry,BankClaim,EvidenceCase
from app.finance import service as finance
from app.bank.service import create_account


def main():
    os.environ['MCSTAR_CONFIG']=str(Path(sys.argv[1]).resolve())
    settings=load_settings()
    options=make_url(settings.database_url).query.get('options','')
    if not str(options).startswith('-csearch_path=test_ui_'):raise RuntimeError('Synthetic UI schema required')
    if any(getattr(settings,key) for key in ['sending_enabled','collection_enabled','model_calls_enabled','phone_delivery_enabled']):raise RuntimeError('External actions must be disabled')
    engine=make_engine(settings.database_url);prefix='FIX3-'+uuid4().hex[:10]
    with session_factory(engine)() as db,db.begin():
        db.info['settings']=settings
        actor=Actor(db.scalar(select(User.id).where(User.username=='admin')),{'*'},set())
        def call(fn,**payload):return fn(db,actor,dict(idempotency_key=uuid4().hex,**payload))
        warehouse=call(finance.create_warehouse,name=prefix)
        party=call(finance.create_party,name=prefix,warehouse_id=warehouse['id'])
        account=call(create_account,warehouse_id=warehouse['id'],bank_name='Synthetic',account_number=prefix,currency='NGN')
        invoice=call(finance.create_invoice,warehouse_id=warehouse['id'],party_id=party['id'],reference=prefix,amount_ngn='100',uploaded_on='2026-11-01')
        payments=[];claims=[]
        for n in range(55):
            payments.append(call(finance.create_payment,warehouse_id=warehouse['id'],party_id=party['id'],amount='10',currency='NGN',paid_on='2026-11-01',source_key=prefix+'-'+str(n))['id'])
            entry=BankEntry(account_id=account['id'],currency='NGN',credit='10',debit='0',transaction_date=date(2026,11,1),reference=prefix+'-'+str(n),description='Synthetic picker fixture',fingerprint=(prefix+str(n)).ljust(64,'0'),issues=[]);db.add(entry);db.flush()
            case=EvidenceCase(warehouse_id=warehouse['id'],account_id=account['id'],business_reference=prefix+'-'+str(n));db.add(case);db.flush()
            claim=BankClaim(entry_id=entry.id,case_id=case.id,state='verified_unallocated',reason='Synthetic typed fixture',actor_id=actor.id);db.add(claim);db.flush();claims.append(claim.id)
    engine.dispose()
    print(json.dumps({'prefix':prefix,'warehouse':warehouse['id'],'party':party['id'],'invoice':invoice['id'],'payments':sorted(payments),'claims':sorted(claims)}))


if __name__=='__main__':main()
