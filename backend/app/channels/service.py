"""Signed ingress and explicit authorized actions. No model text executes money commands."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
from uuid import uuid4
from sqlalchemy import select, text
from app.common import DomainError, require, check_version
from app.models import ChannelMessage, ChannelAction, Delivery, Job, Payment, EvidenceCase, utcnow
from app.finance.service import command, access, record, locked
from .source import require_source,same_boundary


@dataclass(frozen=True)
class ChannelConfig:
    webhook_key: str = field(default='',repr=False)
    api_key: str = field(default='',repr=False)
    base_url: str = field(default='',repr=False)
    instance: str = ''
    generation: str = ''
    capture_session: str = ''
    assistant_session: str = ''
    main_session: str = ''
    since: int = 0
    scope: str = 'live'
    groups: dict = field(default_factory=dict)  # explicit chat -> warehouse mapping
    operators: dict = field(default_factory=dict)  # source sender -> user id
    recipients: tuple = ()

    @classmethod
    def from_env(cls):
        prefix='MCSTAR_WAHA_'
        values={k:os.environ[prefix+k.upper()] for k in cls.__dataclass_fields__ if prefix+k.upper() in os.environ}
        for key in ('groups','operators','recipients'):
            if key in values: values[key]=json.loads(values[key])
        if 'since' in values: values['since']=int(values['since'])
        return cls(**values)


def intake(db,settings,raw,signature,algorithm,config=None):
    config=config or ChannelConfig.from_env()
    if len(raw)>1024*1024: raise DomainError('event_too_large','Webhook body exceeds limit',413)
    if not config.webhook_key: raise DomainError('channel_unconfigured','Webhook signing key unavailable',503)
    expected=hmac.new(config.webhook_key.encode(),raw,'sha512').hexdigest()
    if algorithm!='sha512' or not hmac.compare_digest(signature,expected): raise DomainError('invalid_signature','Invalid webhook signature',401)
    if not settings.collection_enabled: return {'accepted':False,'reason':'collection_paused'}
    if not config.generation or not config.instance or config.since<=0 or config.scope not in ('sample','live'):
        raise DomainError('channel_unconfigured','Explicit instance/generation/start and scope required',503)
    try:
        event=json.loads(raw); p=event['payload']; session=event['session']
        if not isinstance(p,dict): raise ValueError()
        sent=datetime.fromtimestamp(p['timestamp'],timezone.utc)
    except (ValueError,TypeError,KeyError,OverflowError): raise DomainError('invalid_event','Malformed message event') from None
    if session not in (config.capture_session,config.assistant_session): return {'accepted':False,'reason':'session_out_of_scope'}
    if event.get('event') not in ('message','message.any'): return {'accepted':False,'reason':'not_message'}
    if p.get('fromMe') or p.get('isBot'): return {'accepted':False,'reason':'bot_loop'}
    if sent.timestamp()<config.since or sent>utcnow()+timedelta(minutes=5): return {'accepted':False,'reason':'outside_capture_window'}
    group=p.get('from'); sender=p.get('participant') or group
    if group not in config.groups: return {'accepted':False,'reason':'group_out_of_scope'}
    identity=p.get('id')
    if not isinstance(identity,str) or not 1<=len(identity)<=250 or not isinstance(sender,str): raise DomainError('invalid_identity','Stable source message identity required')
    digest=hashlib.sha256(json.dumps(p,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    # Serialize only same message retries; source content conflicts remain visible.
    key=int.from_bytes(hashlib.sha256(f'{config.instance}:{session}:{identity}'.encode()).digest()[:8],'big',signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})
    prior=db.scalar(select(ChannelMessage).where(ChannelMessage.instance==config.instance,ChannelMessage.session==session,ChannelMessage.source_id==identity))
    if prior:
        if not (prior.generation==config.generation and prior.scope==config.scope and prior.warehouse_id==config.groups[group]):
            raise DomainError('source_conflict','Previously accepted provenance cannot be rebound',409)
        if prior.body_hash!=digest: raise DomainError('source_conflict','Same source message has different body',409)
        return {'accepted':True,'id':prior.id,'duplicate':True}
    body=p.get('body')
    if body is not None and (not isinstance(body,str) or len(body)>16000): raise DomainError('invalid_body','Message text too large')
    msg=ChannelMessage(instance=config.instance,session=session,source_id=identity,group_id=group,sender_id=sender,
        sent_at=sent,warehouse_id=config.groups[group],scope=config.scope,generation=config.generation,body=body,
        reply_to=p.get('replyTo'),media_id=identity if p.get('hasMedia') else None,body_hash=digest,state='media_pending' if p.get('hasMedia') else 'received')
    db.add(msg); db.flush()
    if msg.media_id:
        db.add(Job(kind='waha.media',payload={'message_id':msg.id},idempotency_key='waha.media:'+msg.id))
    return {'accepted':True,'id':msg.id,'duplicate':False,'state':msg.state}


def operator_message(db,actor,message_id,config):
    msg=db.get(ChannelMessage,message_id)
    if not msg:raise DomainError('not_found','Source message not found',404)
    require_source(actor,msg,config,'channels.act')
    if config.operators.get(msg.sender_id)!=actor.id or msg.session!=config.assistant_session:
        raise DomainError('operator_not_authorized','Sender is not bound to this authorized operator/session',403)
    return msg


def action_target(db,actor,msg,action,payload):
    if action not in ('payment.post','evidence.review','bank.review'):raise DomainError('unsupported_action','Unsupported assistant action')
    model,cap=(Payment,'finance.post') if action=='payment.post' else (EvidenceCase,'evidence.review' if action=='evidence.review' else 'bank.review')
    target=db.get(model,payload.get('id')) if payload.get('id') else None
    if not target:raise DomainError('not_found','Action target not found',404)
    if model is Payment:
        from app.finance.access import payment_access
        payment_access(db,actor,target,cap)
    else:require(actor,cap,target.warehouse_id)
    if target.warehouse_id!=msg.warehouse_id or target.scope!=msg.scope:
        raise DomainError('action_scope','Message and business target must share warehouse and sample/live scope',403)


def token_for(config,identity):
    if not config.webhook_key: raise DomainError('channel_unconfigured','Action signing configuration unavailable',503)
    return hmac.new(config.webhook_key.encode(),('action:'+identity).encode(),'sha256').hexdigest()


@command('channel.action.prepare','message_id action payload text')
def _prepare_action(db,actor,p):
    config=ChannelConfig.from_env(); msg=operator_message(db,actor,p['message_id'],config)
    if p['action'] not in ('payment.post','evidence.review','bank.review'):
        raise DomainError('unsupported_action','Unsupported assistant action')
    action_target(db,actor,msg,p['action'],p['payload'])
    # The original signed message is authoritative; model-provided text cannot bypass this gate.
    original=msg.body or ''
    if any(v in original for v in ('?','？','如果','假如','是否','能否')) or re.search(r'\b(if|would|could|whether)\b',original,re.I):
        return {'id':msg.id,'status':'question_only','executed':False}
    rows=p['payload'].get('allocations',[])
    if p['action']=='payment.post' and len(rows)!=1:
        return {'id':msg.id,'status':'web_confirmation_required','url':'/finance/payments/'+str(p['payload'].get('id','')),'executed':False}
    action_id=str(uuid4()); token=token_for(config,action_id)
    action=ChannelAction(id=action_id,message_id=msg.id,actor_id=actor.id,nonce_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=utcnow()+timedelta(minutes=10),action=p['action'],payload=p['payload'])
    db.add(action); db.flush(); return {'id':action.id,'status':'confirmation_required','expires_at':action.expires_at,'preview':action.payload,'executed':False}


def prepare_action(db,actor,p):
    result=_prepare_action(db,actor,p)
    if result['status']=='confirmation_required':
        return result|{'confirmation_command':'/confirm '+result['id']+' '+token_for(ChannelConfig.from_env(),result['id'])}
    return result


@command('channel.action.execute','id message_id expected_version')
def execute_action(db,actor,p):
    config=ChannelConfig.from_env(); msg=operator_message(db,actor,p['message_id'],config)
    action=locked(db,ChannelAction,p['id']); check_version(action,p.get('expected_version'))
    if action.actor_id!=actor.id or action.used_at or action.expires_at<=utcnow(): raise DomainError('action_expired','Action expired, used, or belongs to another operator',409)
    origin=operator_message(db,actor,action.message_id,config)
    if not same_boundary(origin,msg): raise DomainError('action_scope','Confirmation source boundary differs',403)
    action_target(db,actor,msg,action.action,action.payload)
    match=re.fullmatch(r'/confirm ([a-f0-9-]{36}) ([a-f0-9]{64})',(msg.body or '').strip())
    if not match or match[1]!=action.id or not hmac.compare_digest(hashlib.sha256(match[2].encode()).hexdigest(),action.nonce_hash):
        raise DomainError('confirmation_required','Explicit signed source-message confirmation required')
    from app.finance.service import post_payment
    from app.evidence.service import review_case as evidence_review
    from app.bank.service import review_case as bank_review
    handler={'payment.post':post_payment,'evidence.review':evidence_review,'bank.review':bank_review}[action.action]
    result=handler(db,actor,{**action.payload,'idempotency_key':'chat-action:'+action.id})
    action.used_at=utcnow(); action.version+=1
    return {'id':action.id,'executed':True,'result':result}


@command('channel.delivery.queue','message_id session recipient text')
def queue_delivery(db,actor,p):
    settings=db.info['settings']; config=ChannelConfig.from_env()
    require(actor,'channels.send')
    if not settings.sending_enabled: raise DomainError('sending_disabled','Sending is disabled',409)
    if not config.api_key or not config.base_url: raise DomainError('channel_unconfigured','Sending provider unavailable',503)
    if p['session']==config.main_session or p['session']!=config.assistant_session or p['recipient'] not in config.recipients:
        raise DomainError('send_scope','Main identity is read-only; approved assistant and target required',403)
    if p.get('message_id'): operator_message(db,actor,p['message_id'],config)
    if not isinstance(p['text'],str) or not 1<=len(p['text'])<=8000: raise DomainError('invalid_body','Delivery text invalid')
    item=Delivery(actor_id=actor.id,session=p['session'],recipient=p['recipient'],text=p['text'],message_id=p.get('message_id'))
    db.add(item); db.flush(); return {'id':item.id,'state':item.state,'version':item.version}
