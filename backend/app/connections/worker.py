"""Leased operations: no database locks held during WAHA or transport IO."""
import io
import os
from datetime import timedelta
from urllib.parse import quote,urlsplit
import httpx
from PIL import Image
from sqlalchemy import select
from app.common import DomainError,require,audit
from app.models import Connection,ConnectionOperation,ConnectionEvent,Outbox,utcnow
from app.attachments import put_file
from app.finance.service import locked
from app.channels.worker import actor_now
from app.channels.service import ChannelConfig,queue_delivery
from .service import boundary,current,observe,recovery_reason,alerts,request_operation,creation_ready
from .transport import probe_transport
from app.channels.http import bounded_request


def provider_ready(config):
    try:
        u=urlsplit(config.base_url)
        return bool(config.api_key and u.scheme in ('https','http') and u.hostname and not u.username and not u.password and not u.query and not u.fragment and u.path in ('','/'))
    except ValueError:return False


async def connection_once(factory,settings,config=None,transport=None):
    from app.migration.operations import worker_gate
    worker_gate(factory,settings)
    environment=ChannelConfig.from_env()
    config=config or ChannelConfig.from_env()
    if not settings.connection_control_enabled and not settings.connection_diagnostics_enabled:return {'status':'connection_disabled','called':False}
    if not provider_ready(config):return {'status':'provider_unavailable','called':False}
    with factory() as db,db.begin():
        db.info['settings']=settings
        for expired in db.scalars(select(ConnectionOperation).where(ConnectionOperation.state=='running',ConnectionOperation.lease_until<utcnow()).with_for_update(skip_locked=True)):
            expired.state='uncertain';expired.outcome='process_interrupted';expired.version+=1
        op=db.scalar(select(ConnectionOperation).where(ConnectionOperation.state=='pending').order_by(ConnectionOperation.created_at).with_for_update(skip_locked=True).limit(1))
        if not op:return {'status':'idle','called':False}
        row=locked(db,Connection,op.connection_id)
        try:
            actor=actor_now(db,op.actor_id);require(actor,'connections.manage')
            if row.operator_id!=op.actor_id:raise DomainError('operator_changed','Connection operator changed')
            if not current(row,config) or boundary(row)!=op.boundary:raise DomainError('connection_boundary','Connection changed')
            if not (settings.connection_diagnostics_enabled if op.kind=='diagnose' else settings.connection_control_enabled):raise DomainError('connection_disabled','Disabled')
            if op.kind=='qr' and row.status!='SCAN_QR_CODE':raise DomainError('qr_unavailable','State changed')
            if op.kind=='connect' and row.status not in ('STOPPED','FAILED','NOT_CONNECTED'):raise DomainError('connection_state','State changed')
            if op.kind=='connect' and row.status=='NOT_CONNECTED' and not creation_ready(config):raise DomainError('webhook_configuration_required','Creation configuration missing')
            if op.kind=='recover':
                reason=recovery_reason(db,row)
                if reason:raise DomainError(reason,'Recovery deferred')
        except DomainError as exc:
            op.state='deferred';op.outcome=exc.code;op.version+=1
            return {'id':op.id,'status':op.state,'called':False,'reason':exc.code}
        op.state='running';op.lease_until=utcnow()+timedelta(seconds=90);op.version+=1
        identity=op.id;cid=row.id;revision=op.revision;kind=op.kind;session=row.session;prior=row.status;lease=op.version;snapshot=op.boundary
        # Actual control requests consume their cap even when the response is uncertain.
        # Diagnose/inspect/QR are not restart attempts.
        if kind in ('recover','connect'):op.attempted_at=utcnow()
    outcome='uncertain';value=None;content=None
    try:
        if kind=='diagnose':
            value=await probe_transport(os.environ.get('MCSTAR_WAHA_TRANSPORT_PROXY_URL',''),5)
            outcome='succeeded'
        else:
            async with httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,timeout=25,headers={'X-Api-Key':config.api_key}) as client:
                base=config.base_url.rstrip('/');encoded=quote(session,safe='')
                if kind=='inspect':response=await bounded_request(client,'GET',base+'/api/sessions/'+encoded)
                elif kind=='qr':response=await bounded_request(client,'GET',base+'/api/'+encoded+'/auth/qr',headers={'Accept':'image/png'})
                else:
                    if prior=='NOT_CONNECTED':
                        target=urlsplit(config.webhook_url)
                        if not config.webhook_key or target.scheme not in ('http','https') or not target.hostname or target.username or target.password or target.fragment:raise ValueError()
                        body={'name':session,'start':True,'config':{'ignore':{'status':True,'channels':True,'broadcast':True,'groups':False},'webhooks':[{'url':config.webhook_url,'events':['message.any','session.status','message.ack'],'hmac':{'key':config.webhook_key}}]}}
                        response=await bounded_request(client,'POST',base+'/api/sessions',json=body)
                    else:
                        endpoint='/start' if prior=='STOPPED' else '/restart'
                        response=await bounded_request(client,'POST',base+'/api/sessions/'+encoded+endpoint,json={})
                if kind=='inspect' and response.status_code==404:value={'name':session,'status':'NOT_CONNECTED'};outcome='succeeded'
                elif response.status_code>=400:outcome='failed'
                elif response.is_redirect:outcome='failed'
                elif kind=='qr':
                    content=response.content
                    if response.headers.get('content-type','').split(';')[0]!='image/png' or len(content)>1024*1024:raise ValueError()
                    with Image.open(io.BytesIO(content)) as im:
                        if im.format!='PNG' or im.width*im.height>4_000_000:raise ValueError()
                        im.verify()
                    outcome='succeeded'
                elif kind=='inspect':value=response.json();outcome='succeeded'
                else:outcome='accepted'
    except (httpx.HTTPError,ValueError,TypeError,OSError,Image.DecompressionBombError):pass
    with factory() as db,db.begin():
        db.info['settings']=settings;op=locked(db,ConnectionOperation,identity);row=locked(db,Connection,cid)
        if op.state!='running' or op.version!=lease:return {'id':identity,'status':'lease_lost','called':True}
        op.state=outcome;op.outcome=outcome;op.lease_until=None;op.version+=1
        try:
            actor=actor_now(db,op.actor_id);require(actor,'connections.manage')
            if row.operator_id!=op.actor_id or ChannelConfig.from_env()!=environment:raise DomainError('operator_changed','Connection configuration changed')
            if not current(row,config) or boundary(row)!=snapshot:raise DomainError('connection_boundary','Changed')
        except DomainError:
            op.outcome='publication_blocked';return {'id':identity,'status':op.outcome,'called':True}
        if revision<=row.observation_revision:op.outcome='stale_response'
        elif kind=='inspect' and outcome=='succeeded':
            if not isinstance(value,dict) or value.get('name',session)!=session:op.outcome='invalid_response'
            else:
                me=value.get('me');observe(db,row,value.get('status','UNKNOWN'),revision,me.get('id') if isinstance(me,dict) else None)
        elif kind=='inspect':observe(db,row,'UNKNOWN',revision)
        elif kind=='qr' and outcome=='succeeded' and row.status=='SCAN_QR_CODE':
            attachment=put_file(db,actor,content,'connection-qr.png','image/png','connection',row.id)
            row.qr_attachment_id=attachment.id;row.qr_expires_at=utcnow()+timedelta(seconds=45);row.observation_revision=revision;row.version+=1
        elif kind=='diagnose' and outcome=='succeeded':
            row.transport_ok=bool(value['ok'] and value['tls_ok'] and value['websocket_ok']);row.transport_checked_at=utcnow();row.version+=1;op.outcome=value['category']
        elif kind in ('connect','recover') and outcome=='accepted':observe(db,row,'STARTING',revision)
        alerts(db,row)
        audit(db,actor,'connection.operation','connection',row.id,None,{'operation_id':op.id,'outcome':op.outcome,'attempted':bool(op.attempted_at)})
        return {'id':identity,'status':op.state,'outcome':op.outcome,'called':True}


def monitor_once(factory,settings):
    from app.migration.operations import worker_gate
    worker_gate(factory,settings)
    """Local observation timers/outbox only. Never creates login or restarts unpaired accounts."""
    with factory() as db,db.begin():
        db.info['settings']=settings;config=ChannelConfig.from_env()
        for row in db.scalars(select(Connection).with_for_update()):
            alerts(db,row)
            if settings.connection_control_enabled and current(row,config) and not recovery_reason(db,row):
                try:
                    actor=actor_now(db,row.operator_id)
                    if not db.scalar(select(ConnectionOperation.id).where(ConnectionOperation.connection_id==row.id,ConnectionOperation.state.in_(['pending','running']))):request_operation(db,actor,{'id':row.id,'expected_version':row.version,'kind':'recover','idempotency_key':'auto-recover:'+row.id+':'+str(row.version)})
                except DomainError:pass
        if not settings.sending_enabled:return {'status':'sending_disabled','called':False}
        out=db.scalar(select(Outbox).where(Outbox.topic=='connection.alert',Outbox.status=='pending').order_by(Outbox.created_at).with_for_update(skip_locked=True).limit(1))
        if not out:return {'status':'idle','called':False}
        event=locked(db,ConnectionEvent,out.payload['event_id']);row=locked(db,Connection,event.connection_id)
        if (event.kind=='offline' and row.status=='WORKING') or (event.kind=='recovered' and row.status!='WORKING'):
            out.status='superseded';return {'status':'superseded','called':False}
        try:
            actor=actor_now(db,out.payload['actor_id']);require(actor,'connections.manage')
            if boundary(row)!=out.payload['boundary'] or row.alert_actor_id!=actor.id or row.alert_recipient!=out.payload['recipient'] or not current(row,config):raise DomainError('connection_boundary','Alert target changed')
            result=queue_delivery(db,actor,{'session':config.assistant_session,'recipient':row.alert_recipient,'text':'连接状态：'+row.role+' '+event.kind,'idempotency_key':'connection-event:'+event.id})
            event.delivery_id=result['id'];out.status='queued'
        except DomainError:out.status='blocked'
        return {'status':out.status,'called':False}
