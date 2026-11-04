"""Owned synthetic 25k/125k benchmark setup. Never accepts a delivery schema."""
import argparse,json,sys,time
from pathlib import Path
from dataclasses import replace,asdict
from uuid import uuid4
from sqlalchemy import text,select
from sqlalchemy.engine import make_url
from argon2 import PasswordHasher
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.settings import load_settings
from app.db import make_engine,session_factory,Base
from app.migration.backup import backup,restore
from app.models import User,Grant


def prepare():
    # Fresh fixture is not a restored writer. Restore evidence stays frozen in
    # the earlier restore_bench schema; no restoration fence is removed.
    from alembic import command
    from app.cli import migration_config
    from app.main import create_app
    from fastapi.testclient import TestClient
    from http_acceptance import run
    source=load_settings();master=make_engine(source.database_url)
    with master.connect() as db:
        if not db.scalar(text('select current_schema()')).startswith('test_ui_'):raise ValueError('Synthetic source only')
    identity=uuid4().hex;root=ROOT/'private/benchmarks'/identity;root.mkdir(parents=True);schema='test_bench_'+identity
    with master.begin() as db:db.execute(text('CREATE SCHEMA '+schema))
    settings=replace(source,database_url=make_url(source.database_url).update_query_dict({'options':'-csearch_path='+schema}).render_as_string(hide_password=False),private_root=root/'runtime',collection_enabled=False,summaries_enabled=False,model_calls_enabled=False,sending_enabled=False,connection_control_enabled=False,phone_delivery_enabled=False,main_account_read_only=True)
    config=migration_config();config.attributes['database_url']=settings.database_url;command.upgrade(config,'head')
    app=create_app(settings);password=PasswordHasher().hash('synthetic-benchmark-password')
    with app.state.session_factory() as db,db.begin():
        for name in ['admin']+['bench'+str(i) for i in range(10)]:
            user=User(username=name,password_hash=PasswordHasher().hash('synthetic-password-123') if name=='admin' else password);db.add(user);db.flush();db.add(Grant(user_id=user.id,capability='*'))
    with TestClient(app) as client:result=run('http://127.0.0.1:8016',client,root/'fixture-http.json')
    (root/'seed-ids.json').write_text(json.dumps(result['synthetic_ids']))
    values=asdict(settings);values['private_root']=str(settings.private_root);profile=root/'runtime.json';profile.write_text(json.dumps(values),encoding='utf-8')
    (ROOT/'private/benchmarks/latest-profile.txt').write_text(str(profile));print(json.dumps({'schema':schema,'profile_path':str(profile),'fixture_business_chain':'passed'}))


def populate(target):
    settings=load_settings();engine=make_engine(settings.database_url);root=settings.private_root.parent
    ids=json.loads((root/'seed-ids.json').read_text());started=time.perf_counter()
    with engine.begin() as db:
        schema=db.scalar(text('select current_schema()'))
        if not schema.startswith('test_bench_') or target not in (25000,125000):raise ValueError('Only fresh owned 25k/125k benchmark is allowed')
        prior=db.scalar(text("select count(*) from tickets where receipt like 'BENCH-%'"))
        if prior>=target:raise ValueError('Population already present')
        def identity(prefix,expression='n'):
            return "md5('bench-"+prefix+"-'||("+expression+")::text)::uuid::text"
        def clone(table,template,overrides,lower,upper):
            columns=list(Base.metadata.tables[table].columns.keys());overrides={'id':identity(table),'created_at':'now()','updated_at':'now()','version':'1',**overrides}
            values=[overrides.get(c,'s."'+c+'"') for c in columns]
            sql='INSERT INTO "'+table+'" ('+','.join('"'+c+'"' for c in columns)+') SELECT '+','.join(values)+' FROM "'+table+'" s CROSS JOIN generate_series(CAST(:lo AS INTEGER),CAST(:hi AS INTEGER)) n WHERE s.id=:seed'
            db.execute(text(sql),{'lo':lower,'hi':upper,'seed':template})
        clone('packing_documents',ids['document'],{'reference':"'BENCH-DOC-'||n"},prior//50+1,target//50)
        clone('receipts',ids['receipt'],{'series':"'BENCH'",'number':'n','reference':"'BENCH-'||lpad(n::text,7,'0')"},prior+1,target)
        spec_id=db.scalar(text('select id from receipt_specs where receipt_id=:id limit 1'),{'id':ids['receipt']})
        clone('receipt_specs',spec_id,{'receipt_id':identity('receipts')},prior+1,target)
        stock_id=db.scalar(text("select id from stock_events where receipt_id=:id and kind='inbound' limit 1"),{'id':ids['receipt']})
        clone('stock_events',stock_id,{'receipt_id':identity('receipts')},prior+1,target)
        clone('tickets',ids['ticket'],{'document_id':identity('packing_documents','(n-1)/50+1'),'native_receipt_id':identity('receipts'),'receipt':"'BENCH-'||lpad(n::text,7,'0')",'invoice_id':'NULL','mark':"'BENCH-GROUP-'||((n-1)/5)::text",'position':'(n-1)%50','review_state':"'pending'"},prior+1,target)
        bank_count=25000 if target==25000 else 50000;case_count=2500 if target==25000 else 5000
        old_bank=db.scalar(text("select count(*) from bank_entries where reference like 'BENCH-BANK-%'"));old_case=db.scalar(text("select count(*) from evidence_cases where business_reference like 'BENCH-CASE-%'"))
        seed_entry=db.scalar(text('select id from bank_entries where account_id=:id limit 1'),{'id':ids['account']});seed_row=db.execute(text('select id,file_id from bank_source_rows where entry_id=:id limit 1'),{'id':seed_entry}).one()
        clone('bank_entries',seed_entry,{'reference':"'BENCH-BANK-'||n",'credit':'1000+(n-1)/50','fingerprint':"md5('bench-bank-'||n)||md5('bench-bank-'||n)",'trusted_id':'NULL','issues':"'[]'::jsonb",'description':"'SYNTHETIC PAYER '||n"},old_bank+1,bank_count)
        clone('bank_source_rows',seed_row.id,{'entry_id':identity('bank_entries'),'row_number':'n+1000','raw_values':"jsonb_build_array('2026-11-01',1000+(n-1)/50,0,'SYNTHETIC PAYER '||n,'BENCH-BANK-'||n)",'parsed':"jsonb_build_object('source_code','synthetic scale fixture')"},old_bank+1,bank_count)
        # A second original file observes the SAME entries, without cloning cash.
        if not old_bank:
            clone('bank_files',seed_row.file_id,{'id':identity('overlap-file'),'sha256':"md5('synthetic-overlap')||md5('synthetic-overlap')"},1,1)
        clone('bank_source_rows',seed_row.id,{'id':identity('overlap-row'),'file_id':identity('overlap-file','1'),'entry_id':identity('bank_entries'),'row_number':'n','raw_values':"jsonb_build_array('2026-11-01',1000+(n-1)/50,0,'SYNTHETIC PAYER '||n,'BENCH-BANK-'||n)",'parsed':"jsonb_build_object('source_code','synthetic overlapping observation')"},old_bank+1,bank_count)
        clone('evidence_cases',ids['case'],{'payment_id':'NULL','state':"'pending'",'amount':'1000+(n-1)/50','transaction_reference':"'BENCH-BANK-'||n",'payer_name':"'SYNTHETIC PAYER '||n",'business_reference':"'BENCH-CASE-'||n"},old_case+1,case_count)
    with engine.begin() as db:db.execute(text('ANALYZE'))
    result={'tickets':target,'receipts':target,'receipt_specs':target,'bank_entries':bank_count,'bank_source_observations':2*bank_count,'evidence_cases':case_count,'equal_amount_bucket':50,'elapsed_seconds':time.perf_counter()-started}
    (root/('population-'+str(target)+'.json')).write_text(json.dumps(result));engine.dispose();print(json.dumps(result))


def enrich():
    from io import BytesIO
    from openpyxl import Workbook
    from app.attachments import put_file
    from app.common import Actor
    from app.models import BankFile
    settings=load_settings();engine=make_engine(settings.database_url);root=settings.private_root.parent
    ids=json.loads((root/'seed-ids.json').read_text())
    with session_factory(engine)() as db,db.begin():
        db.info['settings']=settings
        if not db.scalar(text('select current_schema()')).startswith('test_bench_'):raise ValueError('Fresh synthetic benchmark only')
        count=db.scalar(text("select count(*) from tickets where receipt like 'BENCH-%'"));bank_count=db.scalar(text("select count(*) from bank_entries where reference like 'BENCH-BANK-%'"))
        # Two real specification lines, still exactly 3 PCS / 3 KG / 3 CBM.
        db.execute(text("UPDATE receipt_specs s SET pieces=1,kg=1,cbm=1 FROM receipts r WHERE s.receipt_id=r.id AND r.series='BENCH' AND s.position=0"))
        columns=list(Base.metadata.tables['receipt_specs'].columns.keys());overrides={'id':"md5('bench-extra-spec-'||r.number)::uuid::text",'position':'1','pieces':'2','kg':'2','cbm':'2'}
        expressions=[overrides.get(c,'s."'+c+'"') for c in columns]
        db.execute(text('INSERT INTO receipt_specs ('+','.join('"'+c+'"' for c in columns)+') SELECT '+','.join(expressions)+" FROM receipt_specs s JOIN receipts r ON r.id=s.receipt_id WHERE r.series='BENCH' AND s.position=0 ON CONFLICT DO NOTHING"))
        for position in (0,1):
            columns=list(Base.metadata.tables['cargo_charge_components'].columns.keys())
            overrides={'id':"md5('bench-charge-"+str(position)+"-'||t.id)::uuid::text",'ticket_id':'t.id','ticket_version':'1','quote_id':'NULL','amount':'t.freight_usd' if position==0 else 't.clearing_ngn'}
            expressions=[overrides.get(c,'s."'+c+'"') for c in columns]
            db.execute(text('INSERT INTO cargo_charge_components ('+','.join('"'+c+'"' for c in columns)+') SELECT '+','.join(expressions)+" FROM tickets t CROSS JOIN LATERAL (SELECT * FROM cargo_charge_components WHERE ticket_id=:seed AND position=:position ORDER BY ticket_version DESC LIMIT 1) s WHERE t.receipt LIKE 'BENCH-%' ON CONFLICT DO NOTHING"),{'seed':ids['ticket'],'position':position})
        who=Actor(db.scalar(select(User.id).where(User.username=='bench0')),{'*'},set())
        for overlap in (False,True):
            book=Workbook(write_only=True);sheet=book.create_sheet('Overlap' if overlap else 'Scale')
            sheet.append(['Date','Credit','Debit','Description','Reference'])
            for n in range(1,bank_count+1):sheet.append(['2026-11-01',1000+(n-1)//50,0,'SYNTHETIC PAYER '+str(n),'BENCH-BANK-'+str(n)])
            output=BytesIO();book.save(output);attachment=put_file(db,who,output.getvalue(),'synthetic-scale-'+str(bank_count)+('-overlap' if overlap else '')+'.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','user',who.id)
            file=BankFile(account_id=ids['account'],attachment_id=attachment.id,sha256=attachment.sha256,state='confirmed',mapping={'synthetic_fixture':True});db.add(file);db.flush()
            prefix='overlap-row' if overlap else 'bank_source_rows'
            db.execute(text("UPDATE bank_source_rows s SET file_id=:file,sheet=:sheet,row_number=n+1 FROM generate_series(1,CAST(:count AS INTEGER)) n WHERE s.id=md5('bench-"+prefix+"-'||n)::uuid::text"),{'file':file.id,'sheet':'Overlap' if overlap else 'Scale','count':bank_count})
    path=root/('population-'+str(count)+'.json');data=json.loads(path.read_text());data.update(receipt_specs=2*count,charge_components=2*count,bank_original_workbooks=2,bank_original_rows_per_file=bank_count);path.write_text(json.dumps(data));print(json.dumps({'enriched_tickets':count,'bank_original_rows_per_file':bank_count}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','populate','enrich']);p.add_argument('--tickets',type=int);a=p.parse_args()
    prepare() if a.action=='prepare' else enrich() if a.action=='enrich' else populate(a.tickets)
