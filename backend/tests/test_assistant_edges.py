"""Boundary, delayed-work and actual file integration regressions."""
import asyncio,hashlib,hmac,json
from dataclasses import replace
from io import BytesIO
import httpx,pytest
from sqlalchemy import select
from app.common import DomainError
from app.models import ChannelMessage,DialogueFile,DialogueTurn,Delivery,EvidenceCase,Job
from test_finance import actor,call,seed
from test_channels import configure,event


def test_waha_xlsx_source_is_preserved_and_enters_authorized_thread(app,monkeypatch):
    from openpyxl import Workbook
    from app.channels.service import intake
    from app.channels.worker import media_once
    from app.dialogue.worker import channel_turn_once
    settings=replace(app.state.settings,collection_enabled=True)
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        raw,_=event(cfg,wh,body='请预览银行附件');value=json.loads(raw);value['payload']['hasMedia']=True;raw=json.dumps(value).encode()
        msg=intake(db,settings,raw,hmac.new(cfg.webhook_key.encode(),raw,'sha512').hexdigest(),'sha512',cfg)
    b=Workbook();b.active.append(['Date','Credit']);buf=BytesIO();b.save(buf)
    def transport(request):
        if '/messages/' in request.url.path:return httpx.Response(200,json={'id':'synthetic-message-1','media':{'url':'https://waha.invalid/api/files/bank.xlsx','mimetype':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}})
        return httpx.Response(200,content=buf.getvalue(),headers={'content-type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
    result=asyncio.run(media_once(app.state.session_factory,settings,who.id,cfg,httpx.MockTransport(transport)))
    assert result['status']=='succeeded'
    assert channel_turn_once(app.state.session_factory,settings,cfg)['status']=='pending'
    with app.state.session_factory() as db:
        msg=db.get(ChannelMessage,msg['id']);file=db.scalar(select(DialogueFile));assert msg.attachment_id==file.attachment_id and file.kind=='upload'


def test_formal_export_and_notice_current_source_guard_through_assistant(app):
    from test_documents import prepared
    from app.dialogue import service
    from app.dialogue.files import queue_export,download,notification
    from app.dialogue.inputs import ExportInput
    from app.api.documents_inputs import Render
    from app.documents.exports import export_once
    from app.packing import service as packing
    with app.state.session_factory() as db,db.begin():
        who,wh,doc,ticket=prepared(db,app)
        t=call(db,who,service.create_thread,warehouse_id=wh)
        tr=call(db,who,service.submit_turn,id=t['id'],expected_version=1,text='导出客户箱单，并生成到货通知')
        thread=service.thread_access(db,who,t['id']);turn=db.get(DialogueTurn,tr['id'])
        export=queue_export(db,who,thread,turn,ExportInput(kind='document',payload={'ticket_ids':[ticket['id']],'kind':'packing','format':'xlsx'}))
        notice=notification(db,who,thread,turn,Render(kind='arrival',ticket_ids=[ticket['id']]))
    assert export_once(app.state.settings,app.state.session_factory)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        content,mime=download(db,who,export['id']);assert content[:2]==b'PK' and mime.endswith('sheet')
        text,mt=download(db,who,notice['id']);assert mt=='text/plain' and b'GOODS ARRIVAL' in text
        current=packing.get_ticket(db,who,ticket['id'])
        call(db,who,packing.update_ticket,id=ticket['id'],expected_version=current['version'],mark='Changed source')
        with pytest.raises(DomainError):download(db,who,notice['id'])
        with pytest.raises(DomainError):download(db,who,export['id'])


def test_uncertain_own_message_manual_recovery_preserves_current_operator(app,monkeypatch):
    from app.channels.service import intake,queue_delivery
    from app.channels.origin import resolve_origin
    settings=replace(app.state.settings,collection_enabled=True,sending_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        cfg=replace(cfg,owners={'assistant':'synthetic-operator'},recipients=('synthetic-group',));monkeypatch.setenv('MCSTAR_WAHA_OWNERS',json.dumps(cfg.owners));monkeypatch.setenv('MCSTAR_WAHA_RECIPIENTS',json.dumps(cfg.recipients))
        d=call(db,who,queue_delivery,session='assistant',recipient='synthetic-group',text='bot');db.get(Delivery,d['id']).state='uncertain'
        raw,_=event(cfg,wh,source='human-phone',body='暂缓这张');value=json.loads(raw);value['payload']['fromMe']=True;raw=json.dumps(value).encode()
        held=intake(db,settings,raw,hmac.new(cfg.webhook_key.encode(),raw,'sha512').hexdigest(),'sha512',cfg)
        assert held['state']=='origin_held'
        result=call(db,who,resolve_origin,id=held['id'],expected_version=1,decision='human',reason='Operator verified on phone')
        assert result['origin_state']=='human' and db.get(Delivery,d['id']).state=='uncertain'


def test_assistant_api_requires_csrf_and_current_roles(app):
    from fastapi.testclient import TestClient
    with app.state.session_factory() as db,db.begin():who=actor(db);wh,_=seed(db,who)
    with TestClient(app) as client:
        assert client.get('/api/assistant/threads').status_code==401
        login=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'});csrf=login.json()['csrf_token']
        body={'warehouse_id':wh}
        assert client.post('/api/assistant/threads',json=body,headers={'Idempotency-Key':'csrf-missing'}).status_code==403
        headers={'Idempotency-Key':'create-thread','X-CSRF-Token':csrf}
        created=client.post('/api/assistant/threads',json=body,headers=headers);assert created.status_code==200
        assert client.post('/api/assistant/threads',json=body|{'execute_sql':'DROP'},headers=headers).status_code==422
        assert client.post('/api/assistant/threads',json=body,headers=headers).json()['id']==created.json()['id']


def test_send_original_file_rechecks_current_source_before_io(app,monkeypatch):
    from app.dialogue import service
    from app.dialogue.files import link_file,send_file
    from app.attachments import put_file
    from app.channels.worker import delivery_once
    settings=replace(app.state.settings,sending_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        t=call(db,who,service.create_thread,warehouse_id=wh);tr=call(db,who,service.submit_turn,id=t['id'],expected_version=1,text='发送原文件')
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(width=200,height=200);out=BytesIO();writer.write(out)
        a=put_file(db,who,out.getvalue(),'synthetic.pdf','application/pdf','user',who.id)
        f=link_file(db,who,service.thread_access(db,who,t['id']),db.get(DialogueTurn,tr['id']),'upload',a.id)
        queued=call(db,who,send_file,id=f['id'],expected_version=1,recipient='synthetic-target')
        db.get(DialogueFile,f['id']).attachment_id=None
    calls=[]
    result=asyncio.run(delivery_once(app.state.session_factory,settings,cfg,httpx.MockTransport(lambda r:calls.append(r))))
    assert result['status']=='blocked' and not result['called'] and not calls


def test_media_provider_origin_redirect_limit_and_changed_generation(app,monkeypatch):
    from app.channels.service import intake
    from app.channels.worker import media_once
    settings=replace(app.state.settings,collection_enabled=True,max_attachment_bytes=100)
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        raw,_=event(cfg,wh);value=json.loads(raw);value['payload']['hasMedia']=True;raw=json.dumps(value).encode()
        intake(db,settings,raw,hmac.new(cfg.webhook_key.encode(),raw,'sha512').hexdigest(),'sha512',cfg)
    seen=[]
    def evil(request):
        seen.append(str(request.url));return httpx.Response(200,json={'id':'synthetic-message-1','media':{'url':'https://attacker.invalid/api/files/private'}})
    result=asyncio.run(media_once(app.state.session_factory,settings,who.id,cfg,httpx.MockTransport(evil)))
    assert result['status']=='retry' and len(seen)==1 and 'attacker' not in seen[0]


def test_summary_configuration_paused_and_reuses_first_whole_candidate(app,monkeypatch):
    from app.channels.automation import configure as auto_config,automation_once
    from app.channels.service import intake
    from app.models import ChannelAutomation,ChannelAutomationEvent,RecognitionJob,EvidenceImage
    from test_evidence import image
    from app.evidence.service import create_case
    settings=replace(app.state.settings,collection_enabled=True,summaries_enabled=True,sending_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        raw,sig=event(cfg,wh,body='原图');msg=intake(db,settings,raw,sig,'sha512',cfg)
        attachment=image(db,who);case=call(db,who,create_case,warehouse_id=wh,images=[{'attachment_id':attachment.id,'message_id':msg['id']}])
        im=db.scalar(select(EvidenceImage).where(EvidenceImage.case_id==case['id']))
        from app.models import utcnow
        job=RecognitionJob(image_id=im.id,actor_id=who.id,case_version=1,stage='whole',cache_key='a'*64,state='succeeded',available_at=utcnow(),result={'fields':{'amount':'123'}});db.add(job);db.flush();first=job.id
        config=call(db,who,auto_config,expected_version=0,kind='summary',warehouse_id=wh,scope='live',source_group='synthetic-group',destination='synthetic-target',paused=True)
    assert automation_once(app.state.session_factory,settings,cfg)['status']=='idle'
    with app.state.session_factory() as db,db.begin():db.get(ChannelAutomation,config['id']).paused=False
    assert automation_once(app.state.session_factory,settings,cfg)['status']=='queued'
    with app.state.session_factory() as db:
        observed=db.scalar(select(ChannelAutomationEvent));assert observed.recognition_job_id==first
        assert len(list(db.scalars(select(RecognitionJob))))==1 and '123' in db.get(Delivery,observed.delivery_id).text
    assert automation_once(app.state.session_factory,settings,cfg)['status']=='idle'


def test_delayed_channel_turn_rechecks_generation_before_model_io(app,monkeypatch):
    from app.channels.service import intake
    from app.dialogue.worker import channel_turn_once,dialogue_once
    from app.evidence.recognition import ModelConfig
    settings=replace(app.state.settings,collection_enabled=True,model_calls_enabled=True)
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh);raw,sig=event(cfg,wh,body='查询银行记录');intake(db,settings,raw,sig,'sha512',cfg)
    assert channel_turn_once(app.state.session_factory,settings,cfg)['status']=='pending'
    monkeypatch.setenv('MCSTAR_WAHA_GENERATION','changed-generation');requests=[]
    model=ModelConfig(stage='dialogue',enabled=True,base_url='https://model.invalid/v1',model='synthetic',api_key='synthetic-key-123456')
    result=asyncio.run(dialogue_once(app.state.session_factory,settings,model,httpx.MockTransport(lambda r:requests.append(r))))
    assert result['status']=='blocked' and not result['called'] and not requests


def test_real_file_send_is_provider_submitted_and_never_replayed(app,monkeypatch):
    import base64
    from app.dialogue import service
    from app.dialogue.files import link_file,send_file
    from app.attachments import put_file
    from app.channels.worker import delivery_once
    settings=replace(app.state.settings,sending_enabled=True)
    raw=b'<!doctype html><html><meta charset="utf-8"><p>Synthetic original</p></html>'
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        t=call(db,who,service.create_thread,warehouse_id=wh);tr=call(db,who,service.submit_turn,id=t['id'],expected_version=1,text='发送原文件')
        a=put_file(db,who,raw,'original.html','text/html','user',who.id)
        f=link_file(db,who,service.thread_access(db,who,t['id']),db.get(DialogueTurn,tr['id']),'upload',a.id)
        item=call(db,who,send_file,id=f['id'],expected_version=1,recipient='synthetic-target')
    seen=[]
    def transport(request):
        seen.append(request);payload=json.loads(request.content)
        assert request.url.path=='/api/sendFile' and payload['file']['mimetype']=='text/html' and base64.b64decode(payload['file']['data'])==raw
        return httpx.Response(201,json={'id':'actual-provider-id'})
    result=asyncio.run(delivery_once(app.state.session_factory,settings,cfg,httpx.MockTransport(transport)))
    assert result['status']=='submitted'
    assert asyncio.run(delivery_once(app.state.session_factory,settings,cfg,httpx.MockTransport(transport)))['status']=='idle' and len(seen)==1
    with app.state.session_factory() as db:assert db.get(Delivery,item['id']).receipt=='actual-provider-id'


def test_original_template_archive_worker_preserves_workbook_and_source_scope(app):
    from openpyxl import Workbook,load_workbook
    from app.dialogue import service
    from app.dialogue.files import queue_export,file_once,download
    from app.dialogue.inputs import ExportInput
    from app.attachments import put_file
    from app.bank.service import review_case
    from test_evidence import case,import_bank
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']]);c=case(db,who,wh,account)
        claim=call(db,who,review_case,id=c['id'],expected_version=1,entry_id=entries[0].id,entry_version=1)['claim']
        book=Workbook();sheet=book.active;sheet.append(['编号','日期','Reference','付款人','说明','金额','汇率','人民币汇率','换算','人民币']);sheet['A24']='';sheet['F24'].number_format='#,##0.00';book.create_sheet('Original')['A1']='Preserve original';out=BytesIO();book.save(out)
        a=put_file(db,who,out.getvalue(),'original-template.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','user',who.id)
        t=call(db,who,service.create_thread,warehouse_id=wh);tr=call(db,who,service.submit_turn,id=t['id'],expected_version=1,text='按原模板归档')
        result=queue_export(db,who,service.thread_access(db,who,t['id']),db.get(DialogueTurn,tr['id']),ExportInput(kind='bank_archive',payload={'warehouse_id':wh,'template_attachment_id':a.id,'claims':[{'id':claim['id'],'expected_version':1}]}))
    assert file_once(app.state.session_factory,app.state.settings)['status']=='completed'
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;content,mime=download(db,who,result['id'])
        wb=load_workbook(BytesIO(content),data_only=True);assert wb['Original']['A1'].value=='Preserve original';assert wb.worksheets[0]['F25'].value==100000;wb.close()


def test_nonempty_assistant_migration_downgrade_refuses_to_drop_history(app):
    from alembic import command
    from app.cli import migration_config
    from app.dialogue import service
    from app.models import DialogueThread
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,_=seed(db,who);created=call(db,who,service.create_thread,warehouse_id=wh)
    cfg=migration_config();cfg.attributes['database_url']=app.state.settings.database_url
    with pytest.raises(RuntimeError,match='Refuse to discard'):command.downgrade(cfg,'d11000400003')
    with app.state.session_factory() as db:assert db.get(DialogueThread,created['id']) is not None
