"""Trusted owner identity and exact outbound IDs; text equality is never bot proof."""
from sqlalchemy import select
from app.models import Delivery,ChannelMessage,Job
from app.common import DomainError,require,check_version
from app.finance.service import locked,record
from app.dialogue.commands import command


def review_access(db,actor,p):
    from .service import ChannelConfig
    from .source import require_source
    msg=locked(db,ChannelMessage,p['id']);require_source(actor,msg,ChannelConfig.from_env(),'channels.review')


@command('channel.origin.resolve','id expected_version decision reason',review_access)
def resolve_origin(db,actor,p):
    from .service import ChannelConfig
    cfg=ChannelConfig.from_env();msg=locked(db,ChannelMessage,p['id']);check_version(msg,p.get('expected_version'))
    if not db.info['settings'].collection_enabled:raise DomainError('collection_paused','Collection paused',409)
    if msg.origin_state!='held' or p.get('decision') not in ('human','bot') or not isinstance(p.get('reason'),str) or not p['reason'].strip():raise DomainError('origin_review','Explicit held-source review and reason required')
    if not msg.from_me or cfg.owners.get(msg.session)!=msg.sender_id or cfg.operators.get(msg.sender_id)!=actor.id:raise DomainError('operator_not_authorized','Only trusted phone-owner operator may resolve own ambiguous message',403)
    known=db.scalar(select(Delivery).where(Delivery.receipt==msg.source_id,Delivery.session==msg.session,Delivery.recipient==msg.group_id))
    if known and p['decision']=='human':raise DomainError('known_bot','Exact known outbound ID cannot become a human action',409)
    msg.origin_state=p['decision'];msg.state=('media_pending' if msg.media_id else 'received') if p['decision']=='human' else 'bot_ignored';msg.version+=1
    if msg.media_id and p['decision']=='human' and not db.scalar(select(Job.id).where(Job.idempotency_key=='waha.media:'+msg.id)):db.add(Job(kind='waha.media',payload={'message_id':msg.id},idempotency_key='waha.media:'+msg.id))
    from app.common import audit
    audit(db,actor,'channel.origin.review','channel_message',msg.id,None,{'decision':p['decision'],'reason':p['reason']})
    return {'id':msg.id,'version':msg.version,'origin_state':msg.origin_state,'state':msg.state}


def classify(db,config,session,chat,p,event):
    if not p.get('fromMe'):return p.get('participant') or chat,'human'
    owner=config.owners.get(session)
    participant=p.get('participant') or (event.get('me') or {}).get('id')
    identity=p.get('id')
    known=db.scalar(select(Delivery).where(Delivery.session==session,Delivery.recipient==chat,Delivery.receipt==identity))
    if known and known.boundary and known.boundary.get('instance')==config.instance:return owner or participant or '', 'bot'
    if not owner or (participant and participant!=owner):return participant or '', 'held'
    pending=db.scalar(select(Delivery.id).where(Delivery.session==session,Delivery.recipient==chat,Delivery.state.in_(['sending','uncertain'])))
    return owner,'held' if pending else 'human'


def reconcile(db,config,session,chat):
    for msg in db.scalars(select(ChannelMessage).where(ChannelMessage.instance==config.instance,ChannelMessage.session==session,ChannelMessage.group_id==chat,ChannelMessage.origin_state=='held').with_for_update()):
        sender,state=classify(db,config,session,chat,{'id':msg.source_id,'fromMe':True,'participant':msg.sender_id},{})
        if state=='bot':msg.origin_state='bot';msg.state='bot_ignored';msg.version+=1
        elif state=='human':
            from .source import accepted_source
            if accepted_source(msg,config) and config.operators.get(sender):
                msg.origin_state='human';msg.state='media_pending' if msg.media_id else 'received';msg.version+=1
                if msg.media_id and not db.scalar(select(Job.id).where(Job.idempotency_key=='waha.media:'+msg.id)):
                    db.add(Job(kind='waha.media',payload={'message_id':msg.id},idempotency_key='waha.media:'+msg.id))
