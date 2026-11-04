"""Review regressions: real PostgreSQL domains; external HTTP transports only."""
import asyncio,json
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
import httpx,pytest
from sqlalchemy import select,delete
from fastapi.testclient import TestClient
from app.common import DomainError
from app.models import EvidenceCase,BankAccount,BankEntry,Grant,WarehouseScope,DialogueTurn,Job,Delivery,Connection,Notification,utcnow
from app.dialogue import service
from app.dialogue.worker import dialogue_once,channel_turn_once
from app.channels.worker import delivery_once
from app.channels.service import intake
from app.evidence.recognition import ModelConfig
from test_finance import actor,call,seed
from test_channels import configure,event


def model_config():
    return ModelConfig(stage='dialogue',enabled=True,model='synthetic',api_key='synthetic-key-123456',base_url='https://model.invalid/v1')


@pytest.mark.parametrize('change',['evidence_permission','bank_permission','evidence_scope','bank_scope','evidence_version','bank_version','result_changed','authorized'])
def test_delayed_query_reply_reauthorizes_domain_results(app,monkeypatch,change):
    settings=replace(app.state.settings,collection_enabled=True,sending_enabled=True,model_calls_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        cfg=replace(cfg,recipients=('synthetic-group',));monkeypatch.setenv('MCSTAR_WAHA_RECIPIENTS',json.dumps(cfg.recipients))
        case=EvidenceCase(warehouse_id=wh,scope='live',payer_name='PRIVATE-QUERY');db.add(case);db.flush();cid=case.id
        from app.bank.service import create_account
        aid=call(db,who,create_account,warehouse_id=wh,bank_name='Synthetic',account_number='1234567890',currency='NGN')['id']
        db.add(BankEntry(account_id=aid,currency='NGN',credit=100,debit=0,reference='PRIVATE-BANK',description='Synthetic',fingerprint='synthetic',issues=[]))
        raw,sig=event(cfg,wh,body='查询银行' if change.startswith('bank') else '查询凭证');intake(db,settings,raw,sig,'sha512',cfg)
    assert channel_turn_once(app.state.session_factory,settings,cfg)['status']=='pending'
    count=[]
    def model(request):
        count.append(request)
        args={'kind':'bank','account_id':aid} if change.startswith('bank') else {'kind':'cases'}
        msg={'tool_calls':[{'id':'q','type':'function','function':{'name':'query','arguments':json.dumps(args)}}]} if len(count)==1 else {'content':'done'}
        return httpx.Response(200,json={'choices':[{'message':msg}]})
    assert asyncio.run(dialogue_once(app.state.session_factory,settings,model_config(),httpx.MockTransport(model)))['status']=='completed'
    with app.state.session_factory() as db,db.begin():
        delivery=db.scalar(select(Delivery));assert delivery and delivery.state=='pending'
        turn=db.scalar(select(DialogueTurn));assert turn.result['tools'][0]['result']['items']
        assert delivery.assistant_turn_id==turn.id and delivery.assistant_result_hash and delivery.text==''
        if change.endswith('permission'):
            db.execute(delete(Grant).where(Grant.user_id==who.id))
            for cap in ('assistant.read','assistant.act','channels.read','channels.act','channels.send'):db.add(Grant(user_id=who.id,capability=cap))
            db.add(WarehouseScope(user_id=who.id,warehouse_id=wh))
        elif change=='evidence_scope':db.get(EvidenceCase,cid).scope='sample'
        elif change=='bank_scope':db.get(BankAccount,aid).scope='sample'
        elif change=='evidence_version':db.get(EvidenceCase,cid).version+=1
        elif change=='bank_version':db.scalar(select(BankEntry)).version+=1
        elif change=='result_changed':turn.result={**turn.result,'text':'Changed cached result'}
    sent=[]
    def provider(request):sent.append(request);return httpx.Response(200,json={'id':'synthetic-receipt'})
    result=asyncio.run(delivery_once(app.state.session_factory,settings,cfg,httpx.MockTransport(provider)))
    if change=='authorized':assert result['status']=='submitted' and result['called'] and len(sent)==1
    else:assert result['status']=='blocked' and result['called'] is False and not sent
    with app.state.session_factory() as db:
        assert db.get(EvidenceCase,cid) and db.get(BankEntry,db.scalar(select(BankEntry.id)))
        if change.endswith('permission'):
            from app.channels.worker import actor_now
            db.info['settings']=settings
            history=service.get_thread(db,actor_now(db,who.id),db.scalar(select(DialogueTurn.thread_id)))
            assert history['turns'][0]['result']['availability']=='blocked'
            assert 'PRIVATE-QUERY' not in json.dumps(history) and 'PRIVATE-BANK' not in json.dumps(history)
    assert asyncio.run(delivery_once(app.state.session_factory,settings,cfg,httpx.MockTransport(provider)))['called'] is False
    if change=='authorized':
        from alembic import command
        from app.cli import migration_config
        migration=migration_config();migration.attributes['database_url']=app.state.settings.database_url
        with pytest.raises(RuntimeError,match='Refuse to discard assistant reply provenance'):command.downgrade(migration,'e11000700001')
        with app.state.session_factory() as db:assert db.scalar(select(Delivery)).receipt=='synthetic-receipt'


@pytest.mark.parametrize('kind',['pending','notification'])
@pytest.mark.parametrize('entry',['get','worker'])
def test_stale_history_remains_readable_and_new_turn_can_run(app,kind,entry):
    from app.dialogue.files import queue_export,file_once,notification,download,send_file
    from app.dialogue.inputs import ExportInput
    from app.api.documents_inputs import Render
    from test_documents import prepared
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        if kind=='notification':who,wh,_,ticket=prepared(db,app)
        else:who=actor(db);wh,_=seed(db,who)
        t=call(db,who,service.create_thread,warehouse_id=wh)
        tr=call(db,who,service.submit_turn,id=t['id'],expected_version=1,text='导出完整待核清单并生成通知')
        thread=service.thread_access(db,who,t['id']);turn=db.get(DialogueTurn,tr['id'])
        if kind=='pending':
            c=EvidenceCase(warehouse_id=wh,scope='live',payer_name='OLD-HISTORY');db.add(c);db.flush();cid=c.id
            file=queue_export(db,who,thread,turn,ExportInput(kind='pending'))
        else:file=notification(db,who,thread,turn,Render(kind='arrival',ticket_ids=[ticket['id']]))
        turn.result={'text':'OLD-PRIVATE-BODY','tools':[{'tool':'export' if kind=='pending' else 'notification','result':file}]}
        db.get(Job,turn.job_id).status='completed'
    if kind=='pending':assert file_once(app.state.session_factory,app.state.settings)['status']=='completed'
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        if kind=='pending':db.get(EvidenceCase,cid).version+=1
        else:db.scalar(select(Notification)).generated_on-=timedelta(days=1)
    with TestClient(app) as client:
        client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'})
        response=client.get('/api/assistant/threads/'+t['id'])
        if entry=='get':
            assert response.status_code==200,response.text
            history=response.json();assert history['files'][0]['availability']=='stale'
            assert 'OLD-PRIVATE-BODY' not in response.text and 'nonce' not in json.dumps(history['turns'][0]['result'])
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        with pytest.raises(DomainError):download(db,who,file['id'])
        with pytest.raises(DomainError):call(db,who,send_file,id=file['id'],expected_version=1,recipient='synthetic-target')
        thread=service.thread_access(db,who,t['id']);fresh=call(db,who,service.submit_turn,id=t['id'],expected_version=thread.version,text='继续查询')
    requests=[]
    def provider(request):
        requests.append(request);assert b'OLD-PRIVATE-BODY' not in request.content
        return httpx.Response(200,json={'choices':[{'message':{'content':'No action'}}]})
    result=asyncio.run(dialogue_once(app.state.session_factory,replace(app.state.settings,model_calls_enabled=True),model_config(),httpx.MockTransport(provider)))
    assert result['status']=='completed' and len(requests)==1
    with app.state.session_factory() as db:assert db.get(Job,fresh['job_id']).status=='completed'
    with app.state.session_factory() as db:assert db.get(DialogueTurn,tr['id']).result['text']=='OLD-PRIVATE-BODY'


@pytest.mark.parametrize('key',['GENERATION','ASSISTANT_SESSION'])
def test_connection_get_hides_old_boundary_online_and_qr(app,monkeypatch,key):
    from app.connections.service import configure as configure_connection,observe
    from app.attachments import put_file
    from PIL import Image
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        result=call(db,who,configure_connection,expected_version=0,role='assistant',instance=cfg.instance,generation=cfg.generation,session=cfg.assistant_session)
        row=db.get(Connection,result['id']);observe(db,row,'WORKING',1)
    with TestClient(app) as client:
        client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'})
        assert client.get('/api/connections').json()[0]['online'] is True
        monkeypatch.setenv('MCSTAR_WAHA_'+key,'changed')
        observed=client.get('/api/connections').json()[0]
        assert observed['status']=='WORKING'
        with app.state.session_factory() as db,db.begin():
            db.info['settings']=app.state.settings;row=db.get(Connection,result['id']);observe(db,row,'SCAN_QR_CODE',2)
            b=BytesIO();Image.new('RGB',(8,8)).save(b,format='PNG');a=put_file(db,who,b.getvalue(),'qr.png','image/png','connection',row.id)
            row.qr_attachment_id=a.id;row.qr_expires_at=utcnow()+timedelta(seconds=40)
        qr_available=client.get('/api/connections').json()[0]['qr_available']
        qr_status=client.get('/api/connections/'+result['id']+'/qr').status_code
        assert (observed['online'],qr_available,qr_status)==(False,False,409)


def test_migration_blocks_unbound_legacy_reply_but_preserves_uncertain(app,monkeypatch):
    from alembic import command
    from app.cli import migration_config
    from app.channels.service import queue_delivery
    settings=replace(app.state.settings,collection_enabled=True,sending_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        pending=queue_delivery(db,who,{'session':cfg.assistant_session,'recipient':'synthetic-target','text':'OLD-PRIVATE-RESULT','idempotency_key':'assistant-reply:legacy-pending'})
        uncertain=queue_delivery(db,who,{'session':cfg.assistant_session,'recipient':'synthetic-target','text':'UNCERTAIN-FACT','idempotency_key':'assistant-reply:legacy-uncertain'})
        db.get(Delivery,uncertain['id']).state='uncertain'
    migration=migration_config();migration.attributes['database_url']=app.state.settings.database_url
    command.downgrade(migration,'e11000700001');command.upgrade(migration,'head');command.check(migration)
    with app.state.session_factory() as db:
        row=db.get(Delivery,pending['id']);assert row.state=='blocked' and row.text=='OLD-PRIVATE-RESULT'
        assert db.get(Delivery,uncertain['id']).state=='uncertain'
    sent=[]
    assert asyncio.run(delivery_once(app.state.session_factory,settings,cfg,httpx.MockTransport(lambda r:sent.append(r))))['called'] is False
    assert not sent


def test_changed_current_turn_source_persists_blocked_job(app,monkeypatch):
    from app.models import ChannelMessage
    settings=replace(app.state.settings,collection_enabled=True,model_calls_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        raw,sig=event(cfg,wh,source='first-source',body='查询凭证');intake(db,settings,raw,sig,'sha512',cfg)
    first=channel_turn_once(app.state.session_factory,settings,cfg)
    with app.state.session_factory() as db,db.begin():
        db.get(Job,first['job_id']).status='completed'
        raw,sig=event(cfg,wh,source='second-source',body='继续查询');msg=intake(db,settings,raw,sig,'sha512',cfg)
    second=channel_turn_once(app.state.session_factory,settings,cfg)
    with app.state.session_factory() as db,db.begin():db.get(ChannelMessage,msg['id']).scope='sample'
    calls=[]
    result=asyncio.run(dialogue_once(app.state.session_factory,settings,model_config(),httpx.MockTransport(lambda r:calls.append(r))))
    assert result['status']=='blocked' and result['called'] is False and not calls
    with app.state.session_factory() as db:assert db.get(Job,second['job_id']).status=='blocked'
    assert asyncio.run(dialogue_once(app.state.session_factory,settings,model_config(),httpx.MockTransport(lambda r:calls.append(r))))['status']=='idle'


def test_expired_proposal_history_redacts_nonce_without_deleting_fact(app):
    from app.dialogue.inputs import Propose
    from app.models import DialogueProposal
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        case=EvidenceCase(warehouse_id=wh,scope='live');db.add(case);db.flush()
        t=call(db,who,service.create_thread,warehouse_id=wh)
        tr=call(db,who,service.submit_turn,id=t['id'],expected_version=1,text='暂缓这张凭证')
        turn=db.get(DialogueTurn,tr['id']);thread=service.thread_access(db,who,t['id'])
        result=service.propose(db,who,thread,turn,Propose(action='evidence.review',target_id=case.id,payload={'expected_version':1,'action':'defer'}))
        turn.result={'text':result['confirmation_command'],'tools':[{'tool':'propose','result':result}]}
        db.get(DialogueProposal,result['id']).expires_at=utcnow()-timedelta(seconds=1)
        history=service.get_thread(db,who,t['id'])
        assert result['nonce'] not in json.dumps(history) and history['turns'][0]['result']['availability']=='stale'
        assert db.get(DialogueTurn,tr['id']).result['tools'][0]['result']['nonce']==result['nonce']
