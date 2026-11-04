"""Gated WAHA adapters. Specific new-message fetch only; never login/start/read/chat-history."""
from datetime import timedelta
import io
import zipfile
import base64
from urllib.parse import quote, unquote, urlsplit
import httpx
from PIL import Image
from sqlalchemy import select, or_
from app.common import Actor, DomainError, require
from app.models import Delivery, ChannelMessage, Job, User, Grant, WarehouseScope, utcnow
from app.attachments import put_file,read_file
from app.finance.service import locked
from app.evidence.service import create_case
from .service import ChannelConfig,operator_message
from .source import require_source,accepted_source
from .http import bounded_request


def actor_now(db,identity):
    if not identity:raise DomainError('forbidden','No current operator binding',403)
    user=db.get(User,identity)
    if not user or not user.active: raise DomainError('forbidden','Inactive operator',403)
    return Actor(identity,set(db.scalars(select(Grant.capability).where(Grant.user_id==identity))),set(db.scalars(select(WarehouseScope.warehouse_id).where(WarehouseScope.user_id==identity))))


async def delivery_once(factory,settings,config=None,transport=None):
    from app.migration.operations import worker_gate
    worker_gate(factory,settings)
    environment=ChannelConfig.from_env()
    config=config or ChannelConfig.from_env()
    if not settings.sending_enabled: return {'status':'sending_disabled','called':False}
    if not config.base_url or not config.api_key: return {'status':'provider_unavailable','called':False}
    with factory() as db,db.begin():
        db.info['settings']=settings
        # A process crash after the request may have delivered. Never replay this automatically.
        expired=list(db.scalars(select(Delivery).where(Delivery.state=='sending',Delivery.lease_until<utcnow()).with_for_update(skip_locked=True)))
        for old in expired: old.state='uncertain'; old.version+=1
        item=db.scalar(select(Delivery).where(Delivery.state=='pending').order_by(Delivery.created_at).with_for_update(skip_locked=True).limit(1))
        if not item: return {'status':'idle','called':False}
        try:
            actor=actor_now(db,item.actor_id); require(actor,'channels.send')
            if item.session==config.main_session or item.session!=config.assistant_session or item.recipient not in config.recipients:
                raise DomainError('forbidden','Delivery scope changed',403)
            if not item.boundary or item.boundary!={'instance':config.instance,'generation':config.generation,'assistant_session':config.assistant_session,'since':config.since,'scope':config.scope}:raise DomainError('source_out_of_scope','Delivery deployment changed',403)
            if item.message_id and not settings.collection_enabled:raise DomainError('collection_paused','Source work paused',409)
            if item.message_id:operator_message(db,actor,item.message_id,config)
            from .automation import delivery_guard
            delivery_guard(db,actor,item,settings,config)
            from app.dialogue.delivery import reply_text
            reply=reply_text(db,actor,item)
            artifact=None;notice=None
            if item.artifact_id:
                from app.dialogue.files import delivery_material
                artifact,notice=delivery_material(db,actor,item.artifact_id)
        except DomainError:
            item.state='blocked'; return {'id':item.id,'status':'blocked','called':False}
        item.state='sending'; item.lease_until=utcnow()+timedelta(minutes=5); item.version+=1
        identity=item.id; artifact_id=item.artifact_id;payload={'session':item.session,'chatId':item.recipient,'text':reply if reply is not None else notice if notice is not None else item.text}
        endpoint='/api/sendText'
    state='uncertain'; receipt=None;started=False
    try:
        if artifact:
            content=read_file(settings,artifact)
            if len(content)>settings.max_attachment_bytes:raise DomainError('file_too_large','File exceeds sending limit')
            payload={'session':payload['session'],'chatId':payload['chatId'],'caption':'受授权文件','file':{'mimetype':artifact.media_type,'filename':artifact.filename,'data':base64.b64encode(content).decode('ascii')}};endpoint='/api/sendImage' if artifact.media_type.startswith('image/') else '/api/sendFile'
        # Reauthorize after file IO and immediately before irreversible provider submission.
        with factory() as db,db.begin():
            db.info['settings']=settings;item=locked(db,Delivery,identity);actor=actor_now(db,item.actor_id);require(actor,'channels.send')
            if item.state!='sending':raise DomainError('lease_lost','Send state changed')
            if ChannelConfig.from_env()!=environment:raise DomainError('source_out_of_scope','Runtime channel configuration changed',403)
            if item.session==config.main_session or item.session!=config.assistant_session or item.recipient not in config.recipients:raise DomainError('send_scope','Recipient changed')
            if item.message_id:operator_message(db,actor,item.message_id,config)
            delivery_guard(db,actor,item,settings,config)
            if reply_text(db,actor,item)!=reply:raise DomainError('source_changed','Assistant result source changed')
            if artifact_id:
                latest,latest_notice=delivery_material(db,actor,artifact_id)
                if (artifact and (not latest or latest.id!=artifact.id or latest.sha256!=artifact.sha256)) or latest_notice!=notice:raise DomainError('source_changed','File source changed')
        async with httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,timeout=30) as client:
            started=True
            response=await bounded_request(client,'POST',config.base_url.rstrip('/')+endpoint,headers={'X-Api-Key':config.api_key},json=payload)
            if response.status_code in (200,201):
                value=response.json(); receipt=value.get('id')
                if isinstance(receipt,str) and receipt and len(receipt)<=500: state='submitted'
            elif 300<=response.status_code<500 and response.status_code!=408:state='failed'
    except DomainError:
        with factory() as db,db.begin():item=locked(db,Delivery,identity);item.state='blocked';item.lease_until=None;item.version+=1
        return {'id':identity,'status':'blocked','called':False}
    except (httpx.HTTPError,ValueError,TypeError,AttributeError,OSError):
        if not started:state='blocked'
    with factory() as db,db.begin():
        item=locked(db,Delivery,identity)
        item.state=state; item.receipt=receipt if state=='submitted' else None; item.lease_until=None; item.version+=1
        db.flush()
        from .receipts import link_receipts
        from .origin import reconcile
        if item.receipt:link_receipts(db,config.instance,item.session,item.receipt)
        reconcile(db,config,item.session,item.recipient)
    return {'id':identity,'status':state,'called':started}


async def media_once(factory,settings,operator_id,config=None,transport=None):
    from app.migration.operations import worker_gate
    worker_gate(factory,settings)
    environment=ChannelConfig.from_env()
    config=config or ChannelConfig.from_env()
    if not settings.collection_enabled: return {'status':'collection_paused','called':False}
    if not config.base_url or not config.api_key: return {'status':'provider_unavailable','called':False}
    with factory() as db,db.begin():
        job=db.scalar(select(Job).where(Job.kind=='waha.media',Job.available_at<=utcnow(),or_(Job.status.in_(['pending','retry']),(Job.status=='running') & (Job.locked_at<utcnow()-timedelta(minutes=15)))).order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
        if not job:return {'status':'idle','called':False}
        msg=db.get(ChannelMessage,job.payload['message_id'])
        try:
            actor=actor_now(db,operator_id)
            require_source(actor,msg,config,'channels.collect');require(actor,'channels.read',msg.warehouse_id)
            require(actor,'evidence.write',msg.warehouse_id); require(actor,'evidence.read',msg.warehouse_id)
            if msg.origin_state!='human':raise DomainError('origin_held','Source identity not established',409)
        except DomainError:
            job.status='blocked'; return {'status':'blocked','called':False}
        job.status='running'; job.locked_at=utcnow(); job.attempts+=1; job.version+=1
        identity=job.id; lease=job.version; mid=msg.id; source_id=msg.source_id
        path='/api/{}/chats/{}/messages/{}'.format(quote(msg.session,safe=''),quote(msg.group_id,safe=''),quote(source_id,safe=''))
    content=None; error=None
    try:
        async with httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,timeout=60,headers={'X-Api-Key':config.api_key}) as client:
            response=await bounded_request(client,'GET',config.base_url.rstrip('/')+path,params={'downloadMedia':'true'})
            response.raise_for_status(); value=response.json()
            if value.get('id')!=source_id: raise ValueError()
            media=value.get('media') or {}; url=media.get('url',''); origin=urlsplit(config.base_url); target=urlsplit(url); decoded=unquote(target.path)
            if (target.scheme,target.hostname,target.port)!=(origin.scheme,origin.hostname,origin.port) or target.username or target.password or target.fragment or '%' in decoded or not decoded.startswith('/api/files/') or '\\' in decoded or any(v in ('.','..') for v in decoded.split('/')):
                raise ValueError()
            content=bytearray()
            async with client.stream('GET',url) as downloaded:
                downloaded.raise_for_status()
                async for chunk in downloaded.aiter_bytes():
                    if len(content)+len(chunk)>settings.max_attachment_bytes: raise ValueError()
                    content.extend(chunk)
            raw_bytes=bytes(content)
            declared=media.get('mimetype') or downloaded.headers.get('content-type','').split(';')[0]
            if raw_bytes.startswith(b'PK') and declared=='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
                with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
                    if len(archive.infolist())>10000 or sum(x.file_size for x in archive.infolist())>200*1024*1024 or 'xl/workbook.xml' not in archive.namelist():raise ValueError()
                mime=declared
            elif raw_bytes.startswith(bytes.fromhex('d0cf11e0a1b11ae1')) and declared=='application/vnd.ms-excel':mime=declared
            elif raw_bytes.startswith(b'%PDF-') and declared=='application/pdf':mime=declared
            elif declared=='text/html':
                raw_bytes.decode('utf-8');mime=declared
            else:
                with Image.open(io.BytesIO(content)) as img:
                    mime={'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}.get(img.format)
                    if not mime or img.width*img.height>30_720_000: raise ValueError()
                    img.verify()
    except (httpx.HTTPError,ValueError,KeyError,OSError,Image.DecompressionBombError): error='media_download_failed'
    with factory() as db,db.begin():
        db.info['settings']=settings; job=locked(db,Job,identity)
        if job.status!='running' or job.version!=lease: return {'status':'lease_lost','called':True}
        if error:
            job.status='retry' if job.attempts<3 else 'failed'; job.error_code=error; job.available_at=utcnow()+timedelta(minutes=5*job.attempts)
            return {'status':job.status,'called':True}
        if ChannelConfig.from_env()!=environment:
            job.status='blocked';job.error_code='source_changed';return {'status':'blocked','called':True}
        msg=locked(db,ChannelMessage,mid)
        try:
            actor=actor_now(db,operator_id)
            require_source(actor,msg,config,'channels.collect');require(actor,'channels.read',msg.warehouse_id)
            require(actor,'evidence.write',msg.warehouse_id);require(actor,'evidence.read',msg.warehouse_id)
            if msg.origin_state!='human':raise DomainError('origin_held','Source identity changed',409)
        except DomainError as exc:
            job.status='blocked';job.error_code=exc.code;job.locked_at=None;return {'status':'blocked','called':True}
        attachment=put_file(db,actor,bytes(content),'message-original',mime,'user',actor.id)
        msg.attachment_id=attachment.id
        if not mime.startswith('image/'):
            msg.state='media_saved';msg.version+=1;job.status='succeeded';job.locked_at=None
            return {'status':'succeeded','attachment_id':attachment.id,'called':True}
        result=create_case(db,actor,{'warehouse_id':msg.warehouse_id,'scope':msg.scope,'images':[{'attachment_id':attachment.id,'message_id':msg.id,'source':'waha'}],'idempotency_key':'waha-case:'+msg.id})
        msg.state='evidence_saved'; msg.version+=1; job.status='succeeded'; job.locked_at=None
        return {'status':'succeeded','case_id':result['id'],'called':True}
