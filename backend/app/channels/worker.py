"""Gated WAHA adapters. Specific new-message fetch only; never login/start/read/chat-history."""
from datetime import timedelta
import io
from urllib.parse import quote, unquote, urlsplit
import httpx
from PIL import Image
from sqlalchemy import select, or_
from app.common import Actor, DomainError, require
from app.models import Delivery, ChannelMessage, Job, User, Grant, WarehouseScope, utcnow
from app.attachments import put_file
from app.finance.service import locked
from app.evidence.service import create_case
from .service import ChannelConfig,operator_message
from .source import require_source,accepted_source


def actor_now(db,identity):
    user=db.get(User,identity)
    if not user or not user.active: raise DomainError('forbidden','Inactive operator',403)
    return Actor(identity,set(db.scalars(select(Grant.capability).where(Grant.user_id==identity))),set(db.scalars(select(WarehouseScope.warehouse_id).where(WarehouseScope.user_id==identity))))


async def delivery_once(factory,settings,config=None,transport=None):
    config=config or ChannelConfig.from_env()
    if not settings.sending_enabled: return {'status':'sending_disabled','called':False}
    if not config.base_url or not config.api_key: return {'status':'provider_unavailable','called':False}
    with factory() as db,db.begin():
        # A process crash after the request may have delivered. Never replay this automatically.
        expired=list(db.scalars(select(Delivery).where(Delivery.state=='sending',Delivery.lease_until<utcnow()).with_for_update(skip_locked=True)))
        for old in expired: old.state='uncertain'; old.version+=1
        item=db.scalar(select(Delivery).where(Delivery.state=='pending').order_by(Delivery.created_at).with_for_update(skip_locked=True).limit(1))
        if not item: return {'status':'idle','called':False}
        try:
            actor=actor_now(db,item.actor_id); require(actor,'channels.send')
            if item.session==config.main_session or item.session!=config.assistant_session or item.recipient not in config.recipients:
                raise DomainError('forbidden','Delivery scope changed',403)
            if item.message_id:operator_message(db,actor,item.message_id,config)
        except DomainError:
            item.state='blocked'; return {'id':item.id,'status':'blocked','called':False}
        item.state='sending'; item.lease_until=utcnow()+timedelta(minutes=5); item.version+=1
        identity=item.id; payload={'session':item.session,'chatId':item.recipient,'text':item.text}
    state='uncertain'; receipt=None
    try:
        async with httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,timeout=30) as client:
            response=await client.post(config.base_url.rstrip('/')+'/api/sendText',headers={'X-Api-Key':config.api_key},json=payload)
            if response.status_code==200:
                value=response.json(); receipt=value.get('id')
                if isinstance(receipt,str) and receipt: state='delivered'
    except (httpx.HTTPError,ValueError,TypeError,AttributeError): pass
    with factory() as db,db.begin():
        item=locked(db,Delivery,identity)
        item.state=state; item.receipt=receipt if state=='delivered' else None; item.lease_until=None; item.version+=1
    return {'id':identity,'status':state,'called':True}


async def media_once(factory,settings,operator_id,config=None,transport=None):
    config=config or ChannelConfig.from_env()
    if not settings.collection_enabled: return {'status':'collection_paused','called':False}
    if not config.base_url or not config.api_key: return {'status':'provider_unavailable','called':False}
    with factory() as db,db.begin():
        job=db.scalar(select(Job).where(Job.kind=='waha.media',Job.available_at<=utcnow(),or_(Job.status.in_(['pending','retry']),(Job.status=='running') & (Job.locked_at<utcnow()-timedelta(minutes=15)))).order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
        if not job:return {'status':'idle','called':False}
        msg=db.get(ChannelMessage,job.payload['message_id']); actor=actor_now(db,operator_id)
        try:
            require_source(actor,msg,config,'channels.collect');require(actor,'channels.read',msg.warehouse_id)
            require(actor,'evidence.write',msg.warehouse_id); require(actor,'evidence.read',msg.warehouse_id)
        except DomainError:
            job.status='blocked'; return {'status':'blocked','called':False}
        job.status='running'; job.locked_at=utcnow(); job.attempts+=1; job.version+=1
        identity=job.id; lease=job.version; mid=msg.id; source_id=msg.source_id
        path='/api/{}/chats/{}/messages/{}'.format(quote(msg.session,safe=''),quote(msg.group_id,safe=''),quote(source_id,safe=''))
    content=None; error=None
    try:
        async with httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,timeout=60,headers={'X-Api-Key':config.api_key}) as client:
            response=await client.get(config.base_url.rstrip('/')+path,params={'downloadMedia':'true'})
            response.raise_for_status(); value=response.json()
            if value.get('id')!=source_id: raise ValueError()
            media=value.get('media') or {}; url=media.get('url',''); origin=urlsplit(config.base_url); target=urlsplit(url); decoded=unquote(target.path)
            if (target.scheme,target.hostname,target.port)!=(origin.scheme,origin.hostname,origin.port) or target.username or target.password or target.fragment or not decoded.startswith('/api/files/') or '\\' in decoded or any(v in ('.','..') for v in decoded.split('/')):
                raise ValueError()
            content=bytearray()
            async with client.stream('GET',url) as downloaded:
                downloaded.raise_for_status()
                async for chunk in downloaded.aiter_bytes():
                    if len(content)+len(chunk)>settings.max_attachment_bytes: raise ValueError()
                    content.extend(chunk)
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
        msg=locked(db,ChannelMessage,mid); actor=actor_now(db,operator_id)
        require_source(actor,msg,config,'channels.collect')
        attachment=put_file(db,actor,bytes(content),'message-image',mime,'user',actor.id)
        result=create_case(db,actor,{'warehouse_id':msg.warehouse_id,'scope':msg.scope,'images':[{'attachment_id':attachment.id,'message_id':msg.id,'source':'waha'}],'idempotency_key':'waha-case:'+msg.id})
        msg.state='evidence_saved'; msg.version+=1; job.status='succeeded'; job.locked_at=None
        return {'status':'succeeded','case_id':result['id'],'called':True}
