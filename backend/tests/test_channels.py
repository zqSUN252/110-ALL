import asyncio
from dataclasses import replace
from datetime import timedelta
import hashlib,hmac,json
import httpx
import pytest
from sqlalchemy import select
from app.common import DomainError
from app.models import ChannelMessage,ChannelAction,Delivery,Payment,utcnow
from app.channels.service import ChannelConfig,intake,prepare_action,execute_action,queue_delivery
from app.channels.worker import delivery_once,media_once
from app.finance import service as finance
from test_finance import actor,call,seed


def event(config,wh,source='synthetic-message-1',body='确认审核',timestamp=None):
    raw=json.dumps({'session':config.assistant_session,'event':'message','payload':{'id':source,'timestamp':timestamp or int(utcnow().timestamp()),'from':'synthetic-group','participant':'synthetic-operator','body':body,'fromMe':False}}).encode()
    return raw,hmac.new(config.webhook_key.encode(),raw,'sha512').hexdigest()


def configure(monkeypatch,who,wh):
    config=ChannelConfig(webhook_key='synthetic-webhook-key',api_key='synthetic-api-key',base_url='https://waha.invalid',instance='synthetic',generation='synthetic-generation',capture_session='capture',assistant_session='assistant',main_session='main',since=int(utcnow().timestamp())-60,groups={'synthetic-group':wh},operators={'synthetic-operator':who.id},recipients=('synthetic-target',))
    for key in config.__dataclass_fields__:
        value=getattr(config,key)
        monkeypatch.setenv('MCSTAR_WAHA_'+key.upper(),json.dumps(value) if isinstance(value,(dict,tuple)) else str(value))
    return config


def test_signed_scope_paused_historical_duplicate_and_authorized_shared_actions(app,monkeypatch):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings; who=actor(db); wh,party=seed(db,who); config=configure(monkeypatch,who,wh)
        raw,sig=event(config,wh)
        assert intake(db,app.state.settings,raw,sig,'sha512',config)['reason']=='collection_paused'
        settings=replace(app.state.settings,collection_enabled=True)
        with pytest.raises(DomainError):intake(db,settings,raw,'bad','sha512',config)
        old,oldsig=event(config,wh,timestamp=config.since-1)
        assert intake(db,settings,old,oldsig,'sha512',config)['reason']=='outside_capture_window'
        msg=intake(db,settings,raw,sig,'sha512',config)
        assert intake(db,settings,raw,sig,'sha512',config)['duplicate']
        inv=call(db,who,finance.create_invoice,warehouse_id=wh,party_id=party,reference='SYN',amount_ngn='100',uploaded_on='2026-11-01')
        payment=call(db,who,finance.create_payment,warehouse_id=wh,party_id=party,amount='100',currency='NGN')
        proposal={'id':payment['id'],'expected_version':1,'approval':'manual','allocations':[{'invoice_id':inv['id'],'expected_version':1,'amount_ngn':'100'}]}
        action=call(db,who,prepare_action,message_id=msg['id'],action='payment.post',payload=proposal)
        assert action['status']=='confirmation_required' and db.get(Payment,payment['id']).state=='pending'
        confirm_raw,confirm_sig=event(config,wh,source='synthetic-confirmation',body=action['confirmation_command'])
        confirmation=intake(db,settings,confirm_raw,confirm_sig,'sha512',config)
        result=call(db,who,execute_action,id=action['id'],expected_version=1,message_id=confirmation['id'])
        assert result['executed'] and result['result']['state']=='posted'
        question_raw,question_sig=event(config,wh,source='synthetic-question',body='如果确认这笔会怎样？')
        question=intake(db,settings,question_raw,question_sig,'sha512',config)
        assert call(db,who,prepare_action,message_id=question['id'],action='payment.post',payload=proposal,text='确认')['status']=='question_only'
        multiple={**proposal,'allocations':proposal['allocations']*2}
        assert call(db,who,prepare_action,message_id=msg['id'],action='payment.post',payload=multiple)['status']=='web_confirmation_required'


def test_no_send_disabled_main_or_uncertain(app,monkeypatch):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings; who=actor(db); wh,_=seed(db,who); config=configure(monkeypatch,who,wh)
        with pytest.raises(DomainError) as failure:call(db,who,queue_delivery,session='assistant',recipient='synthetic-target',text='synthetic')
        assert failure.value.code=='sending_disabled'
    calls=[]
    def timeout(request):calls.append(request);raise httpx.ReadTimeout('synthetic timeout')
    fake=httpx.MockTransport(timeout)
    assert asyncio.run(delivery_once(app.state.session_factory,app.state.settings,config,fake))['status']=='sending_disabled'
    assert not calls
    settings=replace(app.state.settings,sending_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings
        with pytest.raises(DomainError):call(db,who,queue_delivery,session='main',recipient='synthetic-target',text='synthetic')
        queued=call(db,who,queue_delivery,session='assistant',recipient='synthetic-target',text='synthetic')
    assert asyncio.run(delivery_once(app.state.session_factory,settings,config,fake))['status']=='uncertain'
    assert asyncio.run(delivery_once(app.state.session_factory,settings,config,fake))['status']=='idle'
    assert len(calls)==1
    with app.state.session_factory() as db:assert db.get(Delivery,queued['id']).state=='uncertain'


def test_specific_message_media_download_creates_evidence_without_history(app,monkeypatch):
    from io import BytesIO
    from PIL import Image
    from app.models import EvidenceImage
    settings=replace(app.state.settings,collection_enabled=True)
    with app.state.session_factory() as db,db.begin():
        who=actor(db); wh,_=seed(db,who);config=configure(monkeypatch,who,wh)
        raw,_=event(config,wh); obj=json.loads(raw);obj['payload']['hasMedia']=True;raw=json.dumps(obj).encode();sig=hmac.new(config.webhook_key.encode(),raw,'sha512').hexdigest()
        msg=intake(db,settings,raw,sig,'sha512',config)
    out=BytesIO();Image.new('RGB',(100,100)).save(out,format='PNG');requests=[]
    def transport(request):
        requests.append(str(request.url))
        if '/messages/' in request.url.path:return httpx.Response(200,json={'id':'synthetic-message-1','media':{'url':'https://waha.invalid/api/files/synthetic.png'}})
        return httpx.Response(200,content=out.getvalue(),headers={'content-type':'image/png'})
    result=asyncio.run(media_once(app.state.session_factory,settings,who.id,config,httpx.MockTransport(transport)))
    assert result['status']=='succeeded' and len(requests)==2
    assert '/messages/synthetic-message-1?' in requests[0]
    with app.state.session_factory() as db:
        assert db.get(ChannelMessage,msg['id']).state=='evidence_saved'
        assert len(list(db.scalars(select(EvidenceImage))))==1
