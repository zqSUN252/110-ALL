"""Migration tests use synthetic sources and real PostgreSQL; never QA-as-live."""
import json
import sqlite3
import hashlib
from decimal import Decimal
from pathlib import Path
import pytest
from sqlalchemy import text
from app.db import make_engine


def sample_source(version=1):
    # Two identical receipt strings are distinct original rows; a group owns one bill.
    return {'version': version, 'data': {
        'logistics_partners': json.dumps([{'id':'p1','name':'Synthetic only'}]),
        'logistics_shipments': json.dumps([{'id':'s1','no':'SHIP','bl':'UNCONFIRMED','mode':'sea','tickets':[
            {'receipt':'R','pieces':2,'cbm':1,'freightUSD':10,'clearingNGN':0},
            {'receipt':'R','pieces':3,'cbm':2,'freightUSD':20,'clearingNGN':0}]}]),
        'logistics_bills': json.dumps([{'id':'b1','no':'B','type':'ar','partnerId':'p1','shipmentNo':'SHIP',
            'ticketKeys':['R','R'],'cur':'NGN','amount':100,'received':40,'discount':5,'status':'partial',
            'date':'2026-01-01','dueDate':'2026-02-01',
            'slips':[{'amountNGN':20,'approved':True},{'amountNGN':20,'approved':True}]}]),
        'logistics_payLedger': json.dumps([{'billId':'b1','amountNGN':40,'cur':'NGN'}]),
        'logistics_users': json.dumps([{'id':'u1','name':'old','password':'never-backup-me'}]),
        'logistics_billTomb':'[]','logistics_shipTomb':'[]','logistics_partnerTomb':'[]',
        'logistics_f6recs':json.dumps([{'no':'F','sent':{'at':1700000000000,'by':'historical text'}}])},
        'tokens':{'secret-session':'old'},'hist':{}}


def test_parser_keeps_rows_group_and_unknown_currency_without_cash():
    from app.migration.source import parse_source
    result = parse_source(json.dumps(sample_source()).encode())
    assert result['controls']['tickets'] == 2
    assert result['controls']['bills'] == 1
    assert result['controls']['slips'] == 2
    assert result['controls']['ledger'] == 1
    assert result['controls']['cash_created'] == 0
    assert result['entities']['tickets'][0]['kg'] is None
    assert result['entities']['bills'][0]['received'] == Decimal('40')
    assert 'never-backup-me' not in json.dumps(result, default=str)


def test_decimal_lexemes_do_not_roundtrip_float():
    from app.migration.source import parse_source
    source = sample_source()
    source['data']['logistics_bills'] = source['data']['logistics_bills'].replace('100,', '100.123456789012,')
    result = parse_source(json.dumps(source).encode())
    assert result['entities']['bills'][0]['amount'] == Decimal('100.123456789012')
    assert any(i['code']=='subcent_control' for i in result['issues'])


def applied(app, source=None):
    from app.migration.source import parse_source
    from app.migration.service import apply_source,plan_source
    from app.common import Actor
    from app.models import User
    from sqlalchemy import select
    parsed=parse_source(json.dumps(source or sample_source()).encode())
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        actor=Actor(db.scalar(select(User.id).where(User.username=='admin')),{'*'},set())
        result=apply_source(db,actor,parsed,plan_source(parsed,'fixture','synthetic'))
    return parsed,result


def test_native_resolution_preserves_shared_invoice_and_historical_received(app):
    parsed,batch=applied(app)
    from app.models import LegacyBill,User,Invoice,Payment,Warehouse,Party,PartyWarehouse
    from app.common import Actor
    from sqlalchemy import select
    from app.migration.service import resolve_bill
    from app.finance.service import invoice_json,adjustment_preview,adjust_invoice
    with app.state.session_factory() as db,db.begin():
        actor=Actor(db.scalar(select(User.id).where(User.username=='admin')),{'*'},set())
        w=Warehouse(name='Confirmed fixture warehouse');db.add(w);db.flush()
        party=Party(warehouse_id=w.id,name='Confirmed fixture party');db.add(party);db.flush()
        db.add(PartyWarehouse(party_id=party.id,warehouse_id=w.id,source='fixture source',active=True));db.flush()
        bill=db.scalar(select(LegacyBill))
        result=resolve_bill(db,actor,bill.id,{'expected_version':bill.version,'party_id':party.id,'warehouse_id':w.id,'source':'explicit source owner identity confirmation','idempotency_key':'resolve'})
        inv=db.get(Invoice,result['native_id'])
        assert invoice_json(db,inv)['outstanding_ngn']=='55.00'
        assert db.scalar(select(__import__('sqlalchemy').func.count()).select_from(Payment))==0
        assert adjustment_preview(db,actor,inv.id,'90')['historical_received_ngn']=='40.00'
        with pytest.raises(Exception,match='historical'):
            adjust_invoice(db,actor,{'id':inv.id,'expected_version':inv.version,'amount_ngn':'20','reason':'review','preview':adjustment_preview(db,actor,inv.id,'20'),'idempotency_key':'unsafe-reprice'})


def test_real_import_idempotence_and_narrow_global_access(app):
    from app.migration.service import apply_source,plan_source,verify_source,legacy_list
    from app.common import Actor,DomainError
    from app.models import User,LegacyBillMember,MigrationBatch,Payment
    from sqlalchemy import select,func
    parsed,result=applied(app)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        actor=Actor(db.scalar(select(User.id).where(User.username=='admin')),{'*'},set())
        again=apply_source(db,actor,parsed,plan_source(parsed,'fixture','synthetic'))
        assert again['created']==0
        assert db.scalar(select(func.count()).select_from(LegacyBillMember))==2
        assert db.scalar(select(func.count()).select_from(Payment))==0

        assert verify_source(db,parsed,result['batch_id'])['state']=='verified'
        user=Actor(actor.id,{'legacy.global_read','finance.read'},set())
        assert legacy_list(db,user,'bills')['items'][0]['historical_received']=='40.000000000000'
        with pytest.raises(DomainError):legacy_list(db,Actor(actor.id,{'finance.read'},{'some-real-warehouse'}),'bills')
        from app.migration.source import parse_source
        changed=sample_source(2)
        bills=json.loads(changed['data']['logistics_bills']);bills[0]['slips'].reverse();bills[0]['slips'].append({'amountNGN':20,'approved':True})
        changed['data']['logistics_bills']=json.dumps(bills)
        p2=parse_source(json.dumps(changed).encode())
        assert apply_source(db,actor,p2,plan_source(p2,'fixture','synthetic'))['state']=='review_required'
        assert legacy_list(db,user,'bills')['total']==1
        assert db.scalar(select(func.count()).select_from(Payment))==0

def test_resolved_sea_group_preserves_null_kg_and_one_invoice(app):
    from app.migration.service import resolve_bill,resolve_shipment
    from app.models import LegacyBill,LegacyShipment,User,Warehouse,Party,PartyWarehouse,Ticket,Invoice
    from app.common import Actor,DomainError
    from sqlalchemy import select
    from app.documents.projection import packing_projection
    from app.packing.service import ticket_change_preview
    applied(app)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        actor=Actor(db.scalar(select(User.id).where(User.username=='admin')),{'*'},set())
        w=Warehouse(name='Confirmed fixture');db.add(w);db.flush()
        party=Party(name='Confirmed fixture',warehouse_id=w.id);db.add(party);db.flush()
        db.add(PartyWarehouse(party_id=party.id,warehouse_id=w.id,source='explicit fixture'));db.flush()
        bill=db.scalar(select(LegacyBill));ship=db.scalar(select(LegacyShipment))
        resolve_bill(db,actor,bill.id,{'expected_version':1,'party_id':party.id,'warehouse_id':w.id,'source':'source identity confirmed','idempotency_key':'r-bill'})
        resolve_shipment(db,actor,ship.id,{'expected_version':1,'warehouse_id':w.id,'source':'source warehouse confirmed','uploaded_on':'2026-01-01','physical_reference':None,'idempotency_key':'r-ship'})
        tickets=list(db.scalars(select(Ticket)))
        assert len(tickets)==2 and all(t.kg is None for t in tickets)
        assert len({t.invoice_id for t in tickets})==1 and tickets[0].invoice_id
        view=packing_projection(db,tickets)
        assert Decimal(view['totals']['invoice_total_ngn']['value'])==100
        assert Decimal(view['totals']['outstanding_ngn']['value'])==55
        assert view['totals']['kg']['value'] is None
        assert all(not r['container'] for r in view['rows'])
        from app.documents.rendering import render_artifact,formal_lines,HEADERS
        from openpyxl import load_workbook
        from io import BytesIO
        exported,_=render_artifact(app.state.settings,view,'xlsx','packing')
        book=load_workbook(BytesIO(exported),data_only=True);column=[key for key,_ in HEADERS].index('invoice_total_ngn')+1
        assert sum(book['Master'].cell(i,column).value or 0 for i in (2,3))==100
        assert sum(line.startswith('Invoice Total (NGN):') for line in formal_lines(view))==1
        book.close()
        with pytest.raises(DomainError,match='group'):
            ticket_change_preview(db,actor,tickets[0].id,{'freight_usd':'5','clearing_ngn':'0','ngn_per_usd':'1'})
        from app.packing.service import split_document,void_preview
        from app.models import PackingDocument
        from test_finance import call
        doc=db.get(PackingDocument,tickets[0].document_id)
        with pytest.raises(DomainError,match='complete'):
            call(db,actor,split_document,id=doc.id,expected_version=doc.version,ticket_ids=[tickets[0].id],reference='synthetic split')
        with pytest.raises(DomainError,match='group'):void_preview(db,actor,doc.id)
        changed=call(db,actor,split_document,id=doc.id,expected_version=doc.version,ticket_ids=[t.id for t in tickets],reference='complete source group')
        assert changed['new_document']['id']==tickets[0].document_id==tickets[1].document_id
        assert len({t.invoice_id for t in tickets})==1
        assert db.get(Invoice,tickets[0].invoice_id).source_key=='legacy:'+bill.id


@pytest.mark.parametrize('transport,known_kg,unit',[('SEA',10,'CBM'),('AIR',10,'CBM'),('EXPRESS',10,'CBM'),('SEA',None,'KG')])
def test_unknown_sea_exception_does_not_relax_known_air_or_kg_basis(app,transport,known_kg,unit):
    from test_finance import actor,call,seed
    from app.models import Ticket,Shipment,PackingDocument
    from app.packing.service import arrive
    from app.packing.allocation import revise
    from app.common import DomainError
    from datetime import date
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,_=seed(db,who)
        ship=Shipment(warehouse_id=wh,reference='synthetic',transport=transport);db.add(ship);db.flush()
        doc=PackingDocument(warehouse_id=wh,shipment_id=ship.id,reference='synthetic',uploaded_on=date(2026,1,1));db.add(doc);db.flush()
        ticket=Ticket(warehouse_id=wh,document_id=doc.id,position=1,receipt='synthetic',transport=transport,pieces=2,kg=known_kg,cbm=1,freight_usd=10,clearing_ngn=0,pricing_authority='review');db.add(ticket);db.flush()
        revise(db,ticket,{'charge_components':[{'component':'freight','currency':'USD','unit':unit,'amount':'10','source':'explicit fixture basis'}]})
        values={'id':ticket.id,'expected_version':1,'pieces':1,'cbm':'.5','occurred_on':'2026-01-02','source':'actual fixture','mode':'sea' if transport=='SEA' else 'air'}
        with pytest.raises(DomainError):call(db,who,arrive,**values,kg=None)
        with pytest.raises(DomainError):call(db,who,arrive,**values,kg='11')



def test_connection_pool_failure_is_bounded_and_redacted(app):
    # Exhausting the actual pool must fail promptly, rather than the default 30s.
    import time
    from sqlalchemy.exc import TimeoutError
    engine = make_engine(app.state.settings.database_url, pool_size=1, max_overflow=0)
    with engine.connect():
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            with engine.connect():
                pass
        assert time.monotonic() - start < 6
    engine.dispose()


def test_sqlite_snapshot_contains_committed_wal_without_source_change(tmp_path):
    from app.migration.sqlite_source import consistent_snapshot
    source_root = tmp_path / 'source'
    source_root.mkdir()
    source = source_root / 'source.sqlite3'
    writer = sqlite3.connect(source)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, body TEXT)')
    writer.commit()
    writer.execute("INSERT INTO messages VALUES (1,'committed in WAL')")
    writer.commit()
    originals = {p.name: p.read_bytes() for p in source_root.glob('source.sqlite3*')}
    acls = sqlite_source_acls(source_root)
    with pytest.raises(ValueError,match='source_snapshot_required'):
        consistent_snapshot(source,tmp_path/'unsafe.sqlite3')
    manifest={'assertion':'owner_frozen_bundle','source':'synthetic owner fixture, writes frozen',
              'files':{name:hashlib.sha256(body).hexdigest() for name,body in originals.items()}}
    with pytest.raises(ValueError,match='source_bundle_changed'):
        consistent_snapshot(source,tmp_path/'bad.sqlite3',frozen_manifest=manifest|{'files':{}})
    result = consistent_snapshot(source, tmp_path / 'acquired' / 'snapshot.sqlite3',frozen_manifest=manifest)
    with sqlite3.connect(tmp_path / 'acquired' / 'snapshot.sqlite3') as check:
        assert check.execute('SELECT body FROM messages').fetchall() == [('committed in WAL',)]
    assert result['tables']['messages']['count'] == 1
    assert {p.name: p.read_bytes() for p in source_root.glob('source.sqlite3*')} == originals
    assert sqlite_source_acls(source_root) == acls
    writer.close()


def sqlite_source_acls(root):
    import os, subprocess
    if os.name != 'nt':
        return {p.name: (p.stat().st_mode,p.stat().st_uid,p.stat().st_gid) for p in (root,*root.iterdir())}
    script = r'''$items=@($env:SOURCE_ACL_CHECK)+[System.IO.Directory]::GetFileSystemEntries($env:SOURCE_ACL_CHECK)
foreach($item in $items){
if([System.IO.Directory]::Exists($item)){$acl=[System.IO.Directory]::GetAccessControl($item)}else{$acl=[System.IO.File]::GetAccessControl($item)}
[System.IO.Path]::GetFileName($item)+':'+$acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::All)
}'''
    return subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],env={**os.environ,'SOURCE_ACL_CHECK':str(root)},capture_output=True,check=True).stdout


def test_sqlite_source_overlap_rejected_before_acl_or_writes(tmp_path,monkeypatch):
    from app.migration import sqlite_source
    source_root=tmp_path/'original';source_root.mkdir();source=source_root/'source.db'
    with sqlite3.connect(source) as db:db.execute('CREATE TABLE evidence(id INTEGER)')
    before=source.read_bytes();acls=sqlite_source_acls(source_root)
    manifest={'assertion':'owner_consistent_backup','source':'synthetic owner backup','files':{source.name:hashlib.sha256(before).hexdigest()}}
    def forbidden(*args):raise AssertionError('ACL helper must not run for overlapping layouts')
    monkeypatch.setattr(sqlite_source,'protect_private_root',forbidden)
    for target,extra in [(source_root/'snapshot.db',{}),(source_root/'child'/'snapshot.db',{}),
                         (tmp_path/'snapshot.db',{}),(tmp_path/'owned'/'snapshot.db',{'bundle_root':str(tmp_path)}),
                         (tmp_path/'owned'/'snapshot.db',{'attachment_root':str(tmp_path/'owned'/'original-images')})]:
        with pytest.raises(ValueError,match='source_output_overlap'):
            sqlite_source.consistent_snapshot(source,target,frozen_manifest=manifest|extra)
        assert not target.exists()
    assert not (tmp_path/'owned').exists() and not (source_root/'child').exists()
    assert source.read_bytes()==before and sqlite_source_acls(source_root)==acls


def test_sqlite_cli_rejects_output_outside_owned_private(tmp_path):
    import subprocess,sys
    from pathlib import Path
    manifest=tmp_path/'manifest.json';manifest.write_text('{}')
    result=subprocess.run([sys.executable,'tools/migration/sqlite_export.py','--source',str(tmp_path/'absent.db'),
        '--manifest',str(manifest),'--snapshot',str(tmp_path/'acquired'/'snapshot.db'),'--output',str(tmp_path/'export'),
        '--classification','synthetic'],capture_output=True,cwd=Path(__file__).resolve().parents[2])
    assert result.returncode==1 and b'ValueError' in result.stderr
    assert not (tmp_path/'acquired').exists() and not (tmp_path/'export').exists()


def test_sea_unknown_kg_actual_batches_export_and_pickup_remain_unknown(app):
    from app.models import LegacyShipment,LegacyBill,Ticket,User,Warehouse,Party,PartyWarehouse,Invoice
    from app.migration.service import resolve_bill,resolve_shipment,resolve_charge_basis
    from app.packing import service as packing
    from app.tracking import service as tracking
    from app.documents import release
    from app.documents.projection import packing_projection
    from app.documents.rendering import render_artifact
    from app.attachments import put_file
    from app.common import Actor,DomainError
    from test_logistics import call
    from sqlalchemy import select
    from PIL import Image
    from io import BytesIO
    source=sample_source();ship=json.loads(source['data']['logistics_shipments']);ship[0]['tickets']=ship[0]['tickets'][:1]
    source['data']['logistics_shipments']=json.dumps(ship)
    applied(app,source)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        actor=Actor(db.scalar(select(User.id).where(User.username=='admin')),{'*'},set())
        w=Warehouse(name='Confirmed fixture');db.add(w);db.flush()
        party=Party(name='Confirmed fixture',warehouse_id=w.id);db.add(party);db.flush();db.add(PartyWarehouse(party_id=party.id,warehouse_id=w.id,source='fixture'));db.flush()
        bill=db.scalar(select(LegacyBill));ship=db.scalar(select(LegacyShipment))
        resolve_bill(db,actor,bill.id,{'expected_version':1,'party_id':party.id,'warehouse_id':w.id,'source':'confirmed source','idempotency_key':'bill'})
        resolve_shipment(db,actor,ship.id,{'expected_version':1,'warehouse_id':w.id,'source':'confirmed source','uploaded_on':'2026-01-01','idempotency_key':'ship'})
        destination=Warehouse(name='Confirmed destination only');db.add(destination);db.flush()
        t=db.scalar(select(Ticket));t.destination_warehouse_id=destination.id
        from app.models import BankAccount,BankAccountWarehouse,BankEntry,BankClaim,EvidenceCase
        from app.bank.service import review_case
        from app.migration.bank_evidence import preview as proof_preview,bind as bind_proof
        from datetime import date
        account=BankAccount(warehouse_id=w.id,scope='live',bank_name='synthetic',account_number='synthetic',currency='NGN');db.add(account);db.flush();db.add(BankAccountWarehouse(account_id=account.id,warehouse_id=w.id,source='confirmed source'))
        entry=BankEntry(account_id=account.id,currency='NGN',credit=40,debit=0,transaction_date=date(2026,1,1),reference='synthetic',description='synthetic source',fingerprint='b'*64,issues=[])
        case=EvidenceCase(account_id=account.id,warehouse_id=w.id,scope='live',currency='NGN',amount=40,transaction_date=date(2026,1,1));db.add_all([entry,case]);db.flush()
        call(db,actor,review_case,id=case.id,expected_version=1,entry_id=entry.id,entry_version=1,action='confirm_manual',reason='explicit same old receipt')
        claim=db.scalar(select(BankClaim));proof={'claim_id':claim.id,'claim_version':claim.version,'case_version':case.version,'entry_version':entry.version,'account_version':account.version,'control_owner_party_id':party.id,'members':[{'bill_id':bill.id,'expected_version':bill.version,'invoice_version':1,'amount_ngn':'40','source':'same original received control'}],'reason':'explicit same source','source':'synthetic original source'}
        bind_proof(db,actor,proof|{'preview':proof_preview(db,actor,proof),'idempotency_key':'source-bank-proof'})
        with pytest.raises(DomainError,match='quantities'):
            call(db,actor,packing.arrive,id=t.id,expected_version=t.version,pieces=1,kg=None,cbm='.5',occurred_on='2026-01-02',source='actual fixture',mode='sea')
        resolve_charge_basis(db,actor,t.id,{'expected_version':t.version,'source':'reviewed original CBM basis','charge_components':[{'component':'freight','currency':'USD','unit':'CBM','amount':'10','source':'original fixture CBM column'},{'component':'clearing','currency':'NGN','unit':'CBM','amount':'0','source':'original fixture clearing column'}],'idempotency_key':'basis'})
        call(db,actor,packing.arrive,id=t.id,expected_version=t.version,pieces=1,kg=None,cbm='.5',occurred_on='2026-01-02',source='actual fixture',mode='sea')
        with pytest.raises(DomainError,match='remaining'):
            call(db,actor,packing.arrive,id=t.id,expected_version=t.version,pieces=2,kg=None,cbm='.5',occurred_on='2026-01-03',source='actual fixture',mode='sea')
        call(db,actor,packing.arrive,id=t.id,expected_version=t.version,pieces=1,kg='8',cbm='.5',occurred_on='2026-01-03',source='actual measured batch',mode='sea')
        assert packing.get_ticket(db,actor,t.id)['arrived']['kg'] is None
        assert t.kg is None and db.get(Invoice,t.invoice_id).amount_ngn==100
        call(db,actor,tracking.set_node,target_type='ticket',target_id=t.id,expected_version=t.version,node='warehouse_arrived',mode='manual',destination_warehouse_id=destination.id,occurred_at='2026-01-03T10:00:00+08:00',source='actual destination receipt')
        view=packing_projection(db,[t]);assert view['rows'][0]['arrival_view']['kg'] is None
        blob,_=render_artifact(app.state.settings,view,'xlsx','packing');assert blob.startswith(b'PK')
        order=call(db,actor,release.issue_order,members=[{'ticket_id':t.id,'expected_version':t.version}],mode='credit',promise_on='2026-12-01',guarantor='fixture')
        operator_id=db.scalar(select(User.id).where(User.username=='operator'))
        upload_actor=Actor(operator_id,{'attachments.write'},{destination.id})
        destination_actor=Actor(operator_id,{'release.read','release.physical'},{destination.id})
        image=BytesIO();Image.new('RGB',(4,4)).save(image,format='PNG');photo=put_file(db,upload_actor,image.getvalue(),'proof.png','image/png','user',operator_id)
        picked=call(db,destination_actor,release.physical_release,order_id=order['id'],expected_version=1,ticket_id=t.id,pieces=1,kg=None,cbm='.5',occurred_at='2026-01-04T10:00:00+08:00',photo_ids=[photo.id])
        assert picked['kg'] is None
        projection=release.eligible_order(db,destination_actor,order['id'])
        assert 'historical_received_ngn' not in json.dumps(projection) and 'basis_hash' not in json.dumps(projection)
        remain=projection['members'][0]['remaining']
        assert remain['kg'] is None and Decimal(remain['cbm'])==Decimal('.5')


def test_new_cash_allocation_and_reversal_preserve_old_received(app):
    from app.migration.service import resolve_bill
    from app.models import User,LegacyBill,Warehouse,Party,PartyWarehouse,Invoice,Payment
    from app.common import Actor,DomainError
    from sqlalchemy import select
    from app.finance import service as finance
    from test_logistics import call
    source=sample_source();bills=json.loads(source['data']['logistics_bills']);bills[0]['received']=80;bills[0]['discount']=0
    source['data']['logistics_bills']=json.dumps(bills);applied(app,source)
    with app.state.session_factory() as db,db.begin():
        actor=Actor(db.scalar(select(User.id).where(User.username=='admin')),{'*'},set())
        w=Warehouse(name='confirmed fixture');db.add(w);db.flush();party=Party(warehouse_id=w.id,name='confirmed fixture');db.add(party);db.flush();db.add(PartyWarehouse(party_id=party.id,warehouse_id=w.id,source='fixture'));db.flush()
        old=db.scalar(select(LegacyBill));resolved=resolve_bill(db,actor,old.id,{'expected_version':1,'warehouse_id':w.id,'party_id':party.id,'source':'confirmed source','idempotency_key':'resolve'})
        inv=db.get(Invoice,resolved['native_id'])
        pay=call(db,actor,finance.create_payment,warehouse_id=w.id,party_id=party.id,amount='30',currency='NGN',paid_on='2026-11-04')
        with pytest.raises(DomainError,match='exceeds'):
            with db.begin_nested():
                call(db,actor,finance.post_payment,id=pay['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':inv.id,'expected_version':inv.version,'amount_ngn':'30'}])
        posted=call(db,actor,finance.post_payment,id=pay['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':inv.id,'expected_version':inv.version,'amount_ngn':'20'}])
        assert posted['available_ngn']=='10.00'
        assert finance.invoice_json(db,inv)['outstanding_ngn']=='0.00'
        call(db,actor,finance.reverse_payment,id=pay['id'],expected_version=posted['version'],reason='explicit new payment reversal',confirmed=True)
        assert finance.invoice_json(db,inv)['historical_received_ngn']=='80.00'
        assert finance.invoice_json(db,inv)['outstanding_ngn']=='20.00'
        assert old.received==80
        assert inv.source_key=='legacy:'+old.id


def test_native_invoice_cannot_forge_historical_namespace_or_lose_typed_control(app):
    from test_finance import actor,call,seed
    from app.finance import service as finance
    from app.models import Invoice
    from app.common import DomainError
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,party=seed(db,who)
        p={'warehouse_id':wh,'party_id':party,'reference':'synthetic','amount_ngn':'10','uploaded_on':'2026-01-01'}
        with pytest.raises(DomainError,match='Historical source links'):call(db,who,finance.create_invoice,**p,source_key='legacy:forged')
        native=call(db,who,finance.create_invoice,**p)
        assert native['historical_received_ngn']=='0.00'
        inv=db.get(Invoice,native['id']);inv.source_key='legacy:synthetic-corruption';db.flush()
        with pytest.raises(DomainError,match='required typed'):finance.invoice_json(db,inv)


def test_historical_discount_excess_is_frozen_not_returned_cash(app):
    from app.migration.service import resolve_bill
    from app.models import LegacyBill,Invoice
    from app.finance import service as finance
    from app.common import DomainError
    from test_finance import actor,call,seed
    from sqlalchemy import select
    source=sample_source();bills=json.loads(source['data']['logistics_bills']);bills[0].update(received=80,discount=20)
    source['data']['logistics_bills']=json.dumps(bills);applied(app,source)
    with app.state.session_factory() as db,db.begin():
        who=actor(db);warehouse,party=seed(db,who);bill=db.scalar(select(LegacyBill))
        result=resolve_bill(db,who,bill.id,{'expected_version':1,'warehouse_id':warehouse,'party_id':party,'source':'confirmed source','idempotency_key':'resolve-discount'})
        inv=db.get(Invoice,result['native_id'])
        preview=finance.adjustment_preview(db,who,inv.id,'90')
        assert preview['historical_review_required'] is True
        assert preview['return_to_balance_ngn']=='0.00'
        assert preview['historical_excess_ngn']=='10.00'
        for discount in (None,'0'):
            preview=finance.adjustment_preview(db,who,inv.id,'90',discount)
            assert preview['historical_review_required'] is True
            with pytest.raises(DomainError,match='historical'):
                call(db,who,finance.adjust_invoice,id=inv.id,expected_version=inv.version,amount_ngn='90',reason='cannot erase source discount',preview=preview,**({'discount_total_ngn':discount} if discount else {}))
        assert inv.amount_ngn==100 and bill.received==80 and bill.discount==20
