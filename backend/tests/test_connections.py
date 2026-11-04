import asyncio
from dataclasses import replace
from datetime import timedelta
import httpx
from sqlalchemy import select
from app.models import utcnow
from test_finance import actor,call,seed


def test_connection_disabled_control_and_diagnostic_make_no_http(app):
    from app.connections.worker import connection_once
    calls=[]
    result=asyncio.run(connection_once(app.state.session_factory,app.state.settings,transport=httpx.MockTransport(lambda r:calls.append(r))))
    assert result['called'] is False and not calls


def test_stale_qr_cannot_overwrite_working_or_phone_verify(app):
    from app.connections.service import configure,observe
    from app.models import Connection
    with app.state.session_factory() as db,db.begin():
        who=actor(db)
        item=call(db,who,configure,role='assistant',session='synthetic',instance='synthetic',generation='v1',expected_version=0)
        row=db.get(Connection,item['id'])
        observe(db,row,'SCAN_QR_CODE',1)
        observe(db,row,'WORKING',3)
        assert observe(db,row,'SCAN_QR_CODE',2) is False
        assert row.status=='WORKING' and row.ever_paired
        observe(db,row,'PHONE_VERIFY',4)
        assert observe(db,row,'WORKING',3) is False
        assert row.status=='PHONE_VERIFY'


def test_phone_owner_from_me_is_human_and_bot_response_race_is_held(app,monkeypatch):
    import hashlib,hmac,json
    from test_channels import configure,event
    from app.channels.service import intake,queue_delivery
    from app.channels.worker import delivery_once
    from app.models import ChannelMessage,Delivery
    settings=replace(app.state.settings,collection_enabled=True,sending_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);config=configure(monkeypatch,who,wh)
        config=replace(config,owners={'assistant':'synthetic-operator'})
        monkeypatch.setenv('MCSTAR_WAHA_OWNERS',json.dumps(config.owners))
        raw,_=event(config,wh);value=json.loads(raw);value['payload'].update(fromMe=True,to='synthetic-group',**{'from':'synthetic-operator'})
        raw=json.dumps(value).encode();sig=hmac.new(config.webhook_key.encode(),raw,'sha512').hexdigest()
        accepted=intake(db,settings,raw,sig,'sha512',config)
        assert accepted['accepted'] and accepted['state']=='received'
        config=replace(config,recipients=('synthetic-group',));monkeypatch.setenv('MCSTAR_WAHA_RECIPIENTS',json.dumps(config.recipients))
        queued=call(db,who,queue_delivery,session='assistant',recipient='synthetic-group',text='bot result')
    def transport(request):
        with app.state.session_factory() as db,db.begin():
            value['payload']['id']='outbound-id';value['payload']['body']='bot result'
            raw=json.dumps(value).encode();sig=hmac.new(config.webhook_key.encode(),raw,'sha512').hexdigest()
            held=intake(db,settings,raw,sig,'sha512',config)
            assert held['state']=='origin_held'
        return httpx.Response(200,json={'id':'outbound-id'})
    result=asyncio.run(delivery_once(app.state.session_factory,settings,config,httpx.MockTransport(transport)))
    assert result['status']=='submitted'
    with app.state.session_factory() as db:
        assert db.get(Delivery,queued['id']).state=='submitted'
        msg=db.scalar(select(ChannelMessage).where(ChannelMessage.source_id=='outbound-id'))
        assert msg.origin_state=='bot' and msg.state=='bot_ignored'


def test_receipts_record_group_participant_not_everyone_and_early_receipt(app,monkeypatch):
    import hashlib,hmac,json
    from test_channels import configure
    from app.channels.service import intake,queue_delivery
    from app.channels.worker import delivery_once
    from app.models import Delivery,DeliveryReceipt
    settings=replace(app.state.settings,sending_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        cfg=replace(cfg,recipients=('synthetic@g.us',));monkeypatch.setenv('MCSTAR_WAHA_RECIPIENTS',json.dumps(cfg.recipients))
        item=call(db,who,queue_delivery,session='assistant',recipient='synthetic@g.us',text='Synthetic')
    def transport(request):
        # Real signed receipt arriving before provider HTTP response is persisted and linked later.
        raw=json.dumps({'event':'message.ack','session':'assistant','payload':{'id':'early-id','chatId':'synthetic@g.us','participant':'member@c.us','ack':3}}).encode()
        with app.state.session_factory() as db,db.begin():assert intake(db,settings,raw,hmac.new(cfg.webhook_key.encode(),raw,'sha512').hexdigest(),'sha512',cfg)['accepted']
        return httpx.Response(200,json={'id':'early-id'})
    assert asyncio.run(delivery_once(app.state.session_factory,settings,cfg,httpx.MockTransport(transport)))['status']=='submitted'
    with app.state.session_factory() as db:
        assert db.get(Delivery,item['id']).state=='submitted'
        receipt=db.scalar(select(DeliveryReceipt));assert receipt.delivery_id==item['id'] and receipt.participant=='member@c.us' and receipt.ack==3


def test_recovery_failed_io_consumes_cooldown_but_transport_defer_does_not(app,monkeypatch):
    from test_channels import configure as channel_config
    from app.connections.service import configure,request_operation,recovery_reason
    from app.connections.worker import connection_once
    from app.models import Connection,ConnectionOperation
    settings=replace(app.state.settings,connection_control_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=channel_config(monkeypatch,who,wh)
        item=call(db,who,configure,role='assistant',session='assistant',instance=cfg.instance,generation=cfg.generation,expected_version=0,recovery_enabled=True)
        row=db.get(Connection,item['id']);row.status='FAILED';row.status_since=utcnow()-timedelta(minutes=6);row.ever_paired=True;row.owner_id='owner@c.us';row.transport_ok=True;row.transport_checked_at=utcnow()
        op=call(db,who,request_operation,id=row.id,expected_version=row.version,kind='recover')
    requests=[]
    def failure(request):requests.append(request);return httpx.Response(503)
    result=asyncio.run(connection_once(app.state.session_factory,settings,cfg,httpx.MockTransport(failure)))
    assert result['called'] and result['status']=='failed' and len(requests)==1
    with app.state.session_factory() as db,db.begin():
        row=db.get(Connection,item['id']);operation=db.get(ConnectionOperation,op['id'])
        assert operation.attempted_at and recovery_reason(db,row)=='cooldown'
        operation.attempted_at=utcnow()-timedelta(minutes=11);row.transport_checked_at=utcnow();db.info['settings']=settings
        later=call(db,who,request_operation,id=row.id,expected_version=row.version,kind='recover')
        row.transport_ok=False
    result=asyncio.run(connection_once(app.state.session_factory,settings,cfg,httpx.MockTransport(failure)))
    assert not result['called'] and result['reason']=='transport_deferred' and len(requests)==1
    with app.state.session_factory() as db:assert db.get(ConnectionOperation,later['id']).attempted_at is None


def test_real_qr_worker_cannot_publish_after_newer_phone_verify(app,monkeypatch):
    import io
    from PIL import Image
    from test_channels import configure as channel_config
    from app.connections.service import configure,request_operation,observe
    from app.connections.worker import connection_once
    from app.models import Connection
    settings=replace(app.state.settings,connection_control_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=channel_config(monkeypatch,who,wh)
        item=call(db,who,configure,role='assistant',session='assistant',instance=cfg.instance,generation=cfg.generation,expected_version=0)
        row=db.get(Connection,item['id']);observe(db,row,'SCAN_QR_CODE',1)
        call(db,who,request_operation,id=row.id,expected_version=row.version,kind='qr')
    out=io.BytesIO();Image.new('RGB',(100,100)).save(out,format='PNG')
    def transport(request):
        assert request.method=='GET' and request.url.path=='/api/assistant/auth/qr'
        with app.state.session_factory() as db,db.begin():
            row=db.get(Connection,item['id']);observe(db,row,'PHONE_VERIFY',row.request_revision+1)
        return httpx.Response(200,content=out.getvalue(),headers={'content-type':'image/png'})
    result=asyncio.run(connection_once(app.state.session_factory,settings,cfg,httpx.MockTransport(transport)))
    assert result['outcome']=='stale_response'
    with app.state.session_factory() as db:
        row=db.get(Connection,item['id']);assert row.status=='PHONE_VERIFY' and row.qr_attachment_id is None


def test_unknown_owner_stays_held_and_cannot_be_recipient_impersonation(app,monkeypatch):
    import hashlib,hmac,json
    from test_channels import configure,event
    from app.channels.service import intake,operator_message
    from app.common import DomainError
    import pytest
    settings=replace(app.state.settings,collection_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        raw,_=event(cfg,wh);payload=json.loads(raw);payload['payload'].update(fromMe=True,participant='synthetic-operator',to='synthetic-group');raw=json.dumps(payload).encode()
        result=intake(db,settings,raw,hmac.new(cfg.webhook_key.encode(),raw,'sha512').hexdigest(),'sha512',cfg)
        assert result['state']=='origin_held'
        with pytest.raises(DomainError):operator_message(db,who,result['id'],cfg)


def test_offline60_recovered20_and_default_alert_outbox_does_not_send(app,monkeypatch):
    from test_channels import configure as channel_config
    from app.connections.service import configure,observe,alerts,recovery_reason
    from app.models import Connection,ConnectionOperation,ConnectionEvent,Outbox,Delivery
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who);cfg=channel_config(monkeypatch,who,wh)
        c=call(db,who,configure,role='assistant',session='assistant',instance=cfg.instance,generation=cfg.generation,expected_version=0,recovery_enabled=True,alert_recipient='synthetic-target')
        row=db.get(Connection,c['id']);now=utcnow();observe(db,row,'FAILED',1,now=now-timedelta(seconds=59));alerts(db,row,now)
        assert not list(db.scalars(select(ConnectionEvent)))
        alerts(db,row,now+timedelta(seconds=1));db.flush();assert len(list(db.scalars(select(ConnectionEvent))))==1
        observe(db,row,'WORKING',2,owner='owner',now=now);alerts(db,row,now+timedelta(seconds=19));assert len(list(db.scalars(select(ConnectionEvent))))==1
        alerts(db,row,now+timedelta(seconds=20));db.flush();assert len(list(db.scalars(select(ConnectionEvent))))==2
        assert not list(db.scalars(select(Delivery))) and all(o.status=='pending' for o in db.scalars(select(Outbox)))
        row.status='FAILED';row.status_since=now-timedelta(minutes=6);row.transport_ok=True;row.transport_checked_at=now
        for minutes in (12,30):db.add(ConnectionOperation(connection_id=row.id,actor_id=who.id,kind='recover',state='uncertain',boundary={},revision=1,attempted_at=now-timedelta(minutes=minutes)))
        db.flush();assert recovery_reason(db,row,now)=='hourly_limit'
        row.ever_paired=False;assert recovery_reason(db,row,now)=='not_eligible'


def test_unknown_signed_receipt_is_retained_for_review_without_delivery_claim(app,monkeypatch):
    import hashlib,hmac,json
    from test_channels import configure
    from app.channels.service import intake
    from app.models import ChannelReceiptObservation
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,_=seed(db,who);cfg=configure(monkeypatch,who,wh)
        raw=json.dumps({'event':'message.ack','session':'assistant','payload':{'unknown_engine_field':'synthetic'}}).encode()
        result=intake(db,app.state.settings,raw,hmac.new(cfg.webhook_key.encode(),raw,'sha512').hexdigest(),'sha512',cfg)
        assert result['state']=='held'
        observed=db.scalar(select(ChannelReceiptObservation));assert observed.state=='held' and observed.payload=={'unknown_engine_field':'synthetic'}
