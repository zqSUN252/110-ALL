"""Task7 synthetic real-PG dialogue acceptance; fake external HTTP only."""
import asyncio
import json
from dataclasses import replace
import httpx
import pytest
from sqlalchemy import select
from app.common import DomainError
from app.models import EvidenceCase, Payment
from test_finance import actor, call, seed


def test_questions_never_prepare_money_and_model_claim_is_not_outcome(app):
    from app.dialogue import service
    from app.dialogue.worker import dialogue_once
    from app.evidence.recognition import ModelConfig
    with app.state.session_factory() as db, db.begin():
        db.info['settings']=app.state.settings
        who=actor(db); wh,party=seed(db,who)
        thread=call(db,who,service.create_thread,warehouse_id=wh,scope='live')
        turn=call(db,who,service.submit_turn,id=thread['id'],expected_version=1,text='如果确认这笔付款会怎样？')
    calls=[]
    def transport(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'已经完成付款'}}]})
    settings=replace(app.state.settings,model_calls_enabled=True)
    config=ModelConfig(stage='dialogue',model='synthetic',base_url='https://model.invalid/v1',api_key='synthetic-key-123456',enabled=True)
    result=asyncio.run(dialogue_once(app.state.session_factory,settings,config,httpx.MockTransport(transport)))
    assert result['status']=='completed'
    with app.state.session_factory() as db:
        db.info['settings']=settings
        detail=service.get_thread(db,who,thread['id'])
        assert detail['turns'][0]['result']['executed'] is False
        assert '已经完成付款' not in detail['turns'][0]['result']['text']
        assert list(db.scalars(select(Payment)))==[]


def test_disabled_dialogue_transport_never_called(app):
    from app.dialogue.worker import dialogue_once
    calls=[]
    result=asyncio.run(dialogue_once(app.state.session_factory,app.state.settings,transport=httpx.MockTransport(lambda r:calls.append(r))))
    assert result['called'] is False and not calls


def test_second_focus_uses_own_version_null_correction_and_nonce_real_db(app):
    from app.dialogue import service
    from app.dialogue.inputs import Propose
    from app.models import DialogueTurn,DialogueProposal
    from app.evidence.service import review_case
    from test_evidence import case,import_bank
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        account,_=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']])
        first=case(db,who,wh,account);second=case(db,who,wh,account)
        second=call(db,who,review_case,id=second['id'],expected_version=1,fields={'payer_name':'Second'},action='correct')
        t=call(db,who,service.create_thread,warehouse_id=wh)
        focus=call(db,who,service.set_focus,id=t['id'],expected_version=1,items=[{'case_id':first['id'],'expected_version':1},{'case_id':second['id'],'expected_version':2}])
        turn=call(db,who,service.submit_turn,id=t['id'],expected_version=2,text='请修正第二张，清空付款人姓名')
        thread=service.thread_access(db,who,t['id']);tr=db.get(DialogueTurn,turn['id'])
        proposal=service.propose(db,who,thread,tr,Propose(action='evidence.review',focus_position=2,payload={'fields':{'payer_name':None},'action':'correct'}))
        assert proposal['preview']['id']==second['id'] and proposal['preview']['expected_version']==2
        assert db.get(EvidenceCase,second['id']).payer_name=='Second'
        done=call(db,who,service.confirm,id=proposal['id'],expected_version=1,nonce=proposal['nonce'])
        assert done['executed'] and db.get(EvidenceCase,second['id']).payer_name is None
        assert db.get(EvidenceCase,first['id']).version==1
        tr.text='如果修正第二张会怎样？'
        refused=service.propose(db,who,thread,tr,Propose(action='evidence.review',target_id=second['id'],payload={'expected_version':3,'action':'defer'}))
        assert refused['status']=='question_only'


def test_complete_pending_export_is_real_html_and_current_permission_guard(app):
    from app.dialogue import service
    from app.dialogue.files import queue_export,file_once,download
    from app.dialogue.inputs import ExportInput
    from app.models import DialogueTurn,DialogueFile
    from app.common import Actor
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        db.add_all([EvidenceCase(warehouse_id=wh,scope='live',payer_name='Synthetic '+str(n)) for n in range(205)])
        t=call(db,who,service.create_thread,warehouse_id=wh)
        tr=call(db,who,service.submit_turn,id=t['id'],expected_version=1,text='导出完整待核清单')
        result=queue_export(db,who,service.thread_access(db,who,t['id']),db.get(DialogueTurn,tr['id']),ExportInput(kind='pending'))
    assert file_once(app.state.session_factory,app.state.settings)['status']=='completed'
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        content,mime=download(db,who,result['id'])
        assert mime=='text/html' and content.startswith(b'<!doctype html>') and content.count(b'<article>')==205
        denied=Actor(who.id,{'assistant.read','evidence.read'},{wh})
        with pytest.raises(DomainError):download(db,denied,result['id'])
        file=db.get(DialogueFile,result['id']);c=db.get(EvidenceCase,file.sources['cases'][0]['id']);c.version+=1
        with pytest.raises(DomainError) as err:download(db,who,result['id'])
        assert err.value.code=='stale_export'


def test_bank_upload_preview_then_nonce_import_actual_rows(app):
    from io import BytesIO
    from openpyxl import Workbook
    from app.attachments import put_file
    from app.bank.service import create_account
    from app.dialogue import service
    from app.dialogue.inputs import PreviewBank,Propose
    from app.dialogue.files import queue_bank_preview,file_once
    from app.models import DialogueTurn,DialogueFile,BankEntry
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        account=call(db,who,create_account,warehouse_id=wh,bank_name='Synthetic',account_number='1234567890',currency='NGN')
        book=Workbook();sheet=book.active;sheet.append(['Date','Credit','Debit','Description','Reference']);sheet.append(['2026-11-01',120000,0,'Synthetic customer','SYN-NEW']);buf=BytesIO();book.save(buf)
        a=put_file(db,who,buf.getvalue(),'synthetic-bank.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','user',who.id)
        t=call(db,who,service.create_thread,warehouse_id=wh)
        tr=call(db,who,service.submit_turn,id=t['id'],expected_version=1,text='预览并导入新银行表',attachment_ids=[a.id])
        result=queue_bank_preview(db,who,service.thread_access(db,who,t['id']),db.get(DialogueTurn,tr['id']),PreviewBank(account_id=account['id'],attachment_id=a.id))
    assert file_once(app.state.session_factory,app.state.settings)['status']=='completed'
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;preview=db.get(DialogueFile,result['id']).sources['preview']
        assert preview['state']=='preview' and not list(db.scalars(select(BankEntry)))
        p=service.propose(db,who,service.thread_access(db,who,t['id']),db.get(DialogueTurn,tr['id']),Propose(action='bank.import.confirm',target_id=preview['id'],payload={'expected_version':1,'account_version':1,'sheets':['Sheet'],'date_order':'auto','trusted_reference':False}))
        done=call(db,who,service.confirm,id=p['id'],expected_version=1,nonce=p['nonce'])
        assert done['executed'] and done['result']['transaction_rows']==1
        assert str(db.scalar(select(BankEntry)).credit)=='120000.00'


def test_model_tool_loop_uses_real_query_and_rejects_unauthorized_action(app):
    from app.dialogue import service
    from app.dialogue.worker import dialogue_once
    from app.evidence.recognition import ModelConfig
    from sqlalchemy import text
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        db.add(EvidenceCase(warehouse_id=wh,scope='live',payer_name='SYNTHETIC-MODEL-QUERY'))
        t=call(db,who,service.create_thread,warehouse_id=wh)
        call(db,who,service.submit_turn,id=t['id'],expected_version=1,text='查询凭证')
    requests=[]
    def transport(request):
        with app.state.session_factory() as db,db.begin():assert db.scalar(text('SELECT pg_try_advisory_xact_lock(110002)')) is True
        requests.append(request)
        if len(requests)==1:return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'id':'q1','type':'function','function':{'name':'query','arguments':json.dumps({'kind':'cases'})}},{'id':'bad','type':'function','function':{'name':'execute_sql','arguments':'{}'}}]}}]})
        return httpx.Response(200,json={'choices':[{'message':{'content':'已完成付款'}}]})
    cfg=ModelConfig(stage='dialogue',enabled=True,model='synthetic',api_key='synthetic-key-123456',base_url='https://model.invalid/v1')
    result=asyncio.run(dialogue_once(app.state.session_factory,replace(app.state.settings,model_calls_enabled=True),cfg,httpx.MockTransport(transport)))
    assert result['status']=='completed' and len(requests)==2
    with app.state.session_factory() as db:
        db.info['settings']=app.state.settings;out=service.get_thread(db,who,t['id'])['turns'][0]['result']
        assert out['tools'][0]['result']['items'][0]['payer_name']=='SYNTHETIC-MODEL-QUERY'
        assert out['tools'][1]['result']['error']=='unsupported_tool' and not out['executed']


def test_saved_query_cannot_leak_after_read_capability_revocation(app):
    from app.dialogue import service
    from app.common import Actor
    from app.models import DialogueTurn
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        c=EvidenceCase(warehouse_id=wh,scope='live',payer_name='PRIVATE');db.add(c);db.flush()
        t=call(db,who,service.create_thread,warehouse_id=wh);tr=call(db,who,service.submit_turn,id=t['id'],expected_version=1,text='查询凭证')
        db.get(DialogueTurn,tr['id']).result={'tools':[{'tool':'query','result':{'kind':'cases','items':[{'id':c.id,'payer_name':'PRIVATE'}]}}]}
        result=service.get_thread(db,Actor(who.id,{'assistant.read'},{wh}),t['id'])
        assert result['turns'][0]['result']['availability']=='blocked'
        assert 'PRIVATE' not in json.dumps(result)
