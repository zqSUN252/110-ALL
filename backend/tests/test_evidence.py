import asyncio
from dataclasses import replace
from datetime import date, timedelta
from io import BytesIO
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import httpx
from openpyxl import Workbook, load_workbook
from PIL import Image
import pytest
from sqlalchemy import select
from app.common import DomainError
from app.models import BankEntry, BankClaim, EvidenceCase, Payment, RecognitionJob, Allocation, utcnow
from app.attachments import put_file
from app.bank import service as bank
from app.evidence import service as evidence
from app.evidence.worker import run_once
from app.evidence.recognition import ModelConfig
from app.finance import service as finance
from test_finance import actor,call,seed


def image(db,who,n=0):
    out=BytesIO(); Image.new('RGB',(100,100),(n,20,30)).save(out,format='PNG')
    return put_file(db,who,out.getvalue(),f'synthetic-{n}.png','image/png','user',who.id)


def import_bank(db,who,warehouse,rows,scope='live',trusted=False):
    account=call(db,who,bank.create_account,warehouse_id=warehouse,scope=scope,bank_name='Access',account_number='0000001234',currency='NGN')
    book=Workbook(); sheet=book.active; sheet.title='Access'; sheet.append(['Date','Credit','Debit','Description','Reference'])
    for row in rows:sheet.append(row)
    out=BytesIO(); book.save(out)
    attachment=put_file(db,who,out.getvalue(),'synthetic-bank.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','user',who.id)
    preview=call(db,who,bank.import_preview,account_id=account['id'],attachment_id=attachment.id)
    confirmed=call(db,who,bank.confirm_import,id=preview['id'],expected_version=1,account_version=1,sheets=['Access'],date_order='dmy',trusted_reference=trusted)
    return account, list(db.scalars(select(BankEntry).where(BankEntry.account_id==account['id']).order_by(BankEntry.reference)))


def case(db,who,warehouse,account,amount='100000',images=1,payer='ALPHA SYNTHETIC',reference='SYNTHETIC001'):
    return call(db,who,evidence.create_case,warehouse_id=warehouse,account_id=account['id'],scope=account['scope'],
        fields={'amount':amount,'currency':'NGN','transaction_date':'2026-11-01','payer_name':payer,'recipient_account':'0000001234','bank_name':'Access','transaction_reference':reference},
        images=[{'attachment_id':image(db,who,n).id,'source':'synthetic'} for n in range(images)])


def test_bank_real_parser_multi_image_post_and_freeze(app):
    with app.state.session_factory() as db, db.begin():
        db.info['settings']=app.state.settings; who=actor(db); wh,party=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']])
        c=case(db,who,wh,account,images=3)
        assert len(c['images'])==3
        match=call(db,who,bank.match_case,id=c['id'],expected_version=1)
        assert match['state']=='matched'
        claim=call(db,who,bank.review_case,id=c['id'],expected_version=1,entry_id=entries[0].id,entry_version=1)
        assert not claim['payment_posted'] and claim['claim']['state']=='verified_unallocated'
        invoices=[call(db,who,finance.create_invoice,party_id=party,warehouse_id=wh,reference=f'SYN-{n}',amount_ngn=str(value),uploaded_on='2026-11-01') for n,value in enumerate((20000,30000,50000))]
        payment=call(db,who,finance.create_payment,party_id=party,warehouse_id=wh,amount='100000',currency='NGN',paid_on='2026-11-01')
        posted=call(db,who,finance.post_payment,id=payment['id'],expected_version=1,approval='bank',case_id=c['id'],case_version=2,
            allocations=[{'invoice_id':i['id'],'expected_version':1,'amount_ngn':i['amount_ngn']} for i in invoices])
        assert posted['available_ngn']=='0.00'
        corrected=call(db,who,evidence.review_case,id=c['id'],expected_version=3,fields={'amount':'99999'},action='correct')
        assert corrected['state']=='disputed'
        assert db.scalar(select(BankClaim).where(BankClaim.case_id==c['id'])).state=='disputed'
        assert finance.get_payment(db,who,payment['id'])['allocated_ngn']=='100000.00'
        assert all(finance.get_invoice(db,who,i['id'])['outstanding_ngn']=='0.00' for i in invoices)


def test_bank_same_amount_identity_missing_dates_tolerance_and_coverage(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings; who=actor(db); wh,party=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100,0,'ALPHA SYNTHETIC','SYNTHETIC001'],['2026-11-01',100,0,'ALPHA SYNTHETIC','SYNTHETIC002'],[None,500,0,'UNKNOWN','SYNTHETIC003']],trusted=True)
        assert len(entries)==3 and entries[2].transaction_date is None
        c=case(db,who,wh,account,amount='153.75')
        match=call(db,who,bank.match_case,id=c['id'],expected_version=1)
        assert match['state']=='review' and len(match['candidates'])==2
        assert not any(x['eligible'] for x in match['candidates'])
        unknown=case(db,who,wh,account,amount='9999')
        assert call(db,who,bank.match_case,id=unknown['id'],expected_version=1)['reason']=='bank_coverage_unknown'
        missing=case(db,who,wh,account,amount='500')
        assert len(call(db,who,bank.match_case,id=missing['id'],expected_version=1)['candidates'])==1
        for entry in entries[:2]:
            c=case(db,who,wh,account,amount='100')
            call(db,who,bank.review_case,id=c['id'],expected_version=1,entry_id=entry.id,entry_version=1,action='confirm_manual',reason='Reviewed distinct original bank IDs')
        assert len(list(db.scalars(select(BankClaim))))==2


def test_real_pg_competing_bank_claim_has_one_winner(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings; who=actor(db); wh,_=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']])
        ids=[case(db,who,wh,account)['id'] for _ in range(2)]; entry_id=entries[0].id
    barrier=Barrier(2)
    def submit(cid):
        barrier.wait()
        try:
            with app.state.session_factory() as db,db.begin():
                return call(db,who,bank.review_case,id=cid,expected_version=1,entry_id=entry_id,entry_version=1)['claim']['state']
        except DomainError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(submit,ids))
    assert results.count('verified_unallocated')==1
    assert sum(r in ('bank_review_required','bank_claimed') for r in results)==1
    with app.state.session_factory() as db:
        assert len(list(db.scalars(select(BankClaim))))==1


def test_recognition_real_json_transport_whole_then_zoom_and_disabled(app,monkeypatch):
    config=ModelConfig(stage='multimodal',enabled=True,base_url='https://synthetic-provider.invalid/v1',api_key='synthetic-private-key',model='synthetic-vision')
    for key,value in {'MULTIMODAL_ENABLED':'true','MULTIMODAL_BASE_URL':config.base_url,'MULTIMODAL_API_KEY':config.api_key,'MULTIMODAL_MODEL':config.model}.items():monkeypatch.setenv(key,value)
    settings=replace(app.state.settings,model_calls_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings; who=actor(db); wh,_=seed(db,who)
        c=call(db,who,evidence.create_case,warehouse_id=wh,images=[{'attachment_id':image(db,who).id}])
        queued=call(db,who,evidence.recognize,id=c['id'],expected_version=1)
        assert queued['provider_available']
    seen=[]
    def transport(request):
        payload=json.loads(request.content); seen.append(payload)
        result={'is_receipt':True,'fields':{'amount':'100.00','currency':'NGN','transaction_date':'2026-11-01'},'raw_text':'Amount NGN 100.00 Date 2026-11-01','evidence':{'amount':'Amount NGN 100.00','currency':'NGN','transaction_date':'2026-11-01'},'uncertain_fields':[]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}],'usage':{'total_tokens':10}})
    fake=httpx.MockTransport(transport)
    assert asyncio.run(run_once(app.state.session_factory,app.state.settings,config,fake))['status']=='provider_unavailable'
    first=asyncio.run(run_once(app.state.session_factory,settings,config,fake)); assert first['status']=='succeeded'
    second=asyncio.run(run_once(app.state.session_factory,settings,config,fake)); assert second['status']=='succeeded' and second['stage']=='zoom'
    assert len(seen)==2 and sum(p['type']=='image_url' for p in seen[1]['messages'][0]['content'])>1
    with app.state.session_factory() as db:
        assert len(list(db.scalars(select(Payment))))==0
        assert db.get(EvidenceCase,c['id']).amount is None  # proposals require explicit review
        assert all(j.result['fields']['amount']=='100.00' for j in db.scalars(select(RecognitionJob)))
        assert all(j.state=='succeeded' for j in db.scalars(select(RecognitionJob)))


def test_original_ngn_archive_package_and_typed_readback(app):
    from app.bank.archive import export_archive
    from app.attachments import read_file
    from app.models import Attachment
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings; who=actor(db); wh,_=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']])
        c=case(db,who,wh,account)
        claim=call(db,who,bank.review_case,id=c['id'],expected_version=1,entry_id=entries[0].id,entry_version=1)['claim']
        book=Workbook();sheet=book.active;sheet.title='原NGN模板';sheet.append(['编号','日期','Reference','付款人','说明','金额','汇率','人民币汇率','换算','人民币']);sheet.append(['保留原文字']);sheet.append([None]*10)
        sheet['A24']='';sheet['F24'].number_format='#,##0.00';book.create_sheet('保留副表')['A1']='原模板内容'
        buf=BytesIO();book.save(buf)
        template=put_file(db,who,buf.getvalue(),'synthetic-template.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','user',who.id)
        result=call(db,who,export_archive,warehouse_id=wh,template_attachment_id=template.id,claims=[{'id':claim['id'],'expected_version':1}],label='Synthetic ')
        assert result['round_trip_verified'] and result['written']==1 and result['total_ngn']=='100000.00' and not result['sent']
        exported=load_workbook(BytesIO(read_file(app.state.settings,db.get(Attachment,result['id']))),data_only=True)
        assert exported['保留副表']['A1'].value=='原模板内容'
        assert exported.worksheets[0]['F25'].value==100000 and exported.worksheets[0]['B25'].value.date()==date(2026,11,1)
        exported.close()


def test_supplement_existing_manual_payment_does_not_change_cash_allocation_or_approval(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,party=seed(db,who)
        invoice=call(db,who,finance.create_invoice,party_id=party,warehouse_id=wh,reference='SYN',amount_ngn='100000',uploaded_on='2026-11-01')
        payment=call(db,who,finance.create_payment,party_id=party,warehouse_id=wh,amount='100000',currency='NGN',paid_on='2026-11-01')
        posted=call(db,who,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':invoice['id'],'expected_version':1,'amount_ngn':'100000'}])
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']])
        c=case(db,who,wh,account);call(db,who,bank.review_case,id=c['id'],expected_version=1,entry_id=entries[0].id,entry_version=1)
        linked=call(db,who,finance.attach_bank_evidence,id=payment['id'],expected_version=2,case_id=c['id'],case_version=2)
        assert linked['amount_ngn']==posted['amount_ngn'] and linked['approved_at']==posted['approved_at'] and linked['allocated_ngn']==posted['allocated_ngn']
        assert linked['approval']=='manual' and linked['bank_state']=='verified'


def test_real_legacy_xls_preview_confirm_preserves_original_rows(app):
    import xlwt
    from app.models import BankSourceRow
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        account=call(db,who,bank.create_account,warehouse_id=wh,bank_name='Access',account_number='0000001234',currency='NGN')
        book=xlwt.Workbook();sheet=book.add_sheet('Legacy')
        rows=[['Date','Credit','Debit','Description','Reference'],['2026-11-01',100,0,'SYNTHETIC','SYN-XLS-01'],['',200,0,'MISSING DATE','SYN-XLS-02'],['bad', 'not-money',0,'REJECTED','SYN-XLS-03']]
        for r,values in enumerate(rows):
            for c,value in enumerate(values):sheet.write(r,c,value)
        buf=BytesIO();book.save(buf)
        attachment=put_file(db,who,buf.getvalue(),'synthetic.xls','application/vnd.ms-excel','user',who.id)
        preview=call(db,who,bank.import_preview,account_id=account['id'],attachment_id=attachment.id)
        assert preview['row_count']==4
        confirmed=call(db,who,bank.confirm_import,id=preview['id'],expected_version=1,account_version=1,sheets=['Legacy'])
        assert confirmed['transaction_rows']==2
        assert len(list(db.scalars(select(BankSourceRow))))==4
        assert len(list(db.scalars(select(BankEntry))))==2


def test_identical_untrusted_rows_remain_two_and_both_require_review(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        row=['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']
        account,entries=import_bank(db,who,wh,[row,row])
        assert len(entries)==2 and entries[0].id!=entries[1].id
        assert all('possible_cross_file_duplicate' in e.issues for e in entries)
        c=case(db,who,wh,account)
        result=call(db,who,bank.match_case,id=c['id'],expected_version=1)
        assert result['state']=='review' and len(result['candidates'])==2


def test_bank_http_never_public_and_cross_scope_rejected(app):
    from fastapi.testclient import TestClient
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,party=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']],scope='sample')
        img=image(db,who)
        cid=case(db,who,wh,account)['id']
    with TestClient(app) as client:
        assert client.get('/evidence/'+cid).status_code==404
        assert client.get('/api/evidence/'+cid).status_code==401
        assert client.get('/api/finance/records/bank-accounts',params={'warehouse_id':wh}).status_code==401
        login=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        result=client.post('/api/evidence',json={'warehouse_id':wh,'scope':'live','account_id':account['id'],'images':[{'attachment_id':img.id}]},headers={'X-CSRF-Token':login['csrf_token'],'Idempotency-Key':'cross-scope'})
        assert result.status_code==400 and result.json()['error']['code']=='scope_mismatch'
