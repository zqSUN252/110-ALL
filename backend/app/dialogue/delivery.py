"""Replies retain a typed turn relationship; text alone never conveys tool authority."""
from app.common import DomainError
from app.models import DialogueTurn,Job,Delivery
from app.finance.service import locked
from app.channels.service import ChannelConfig,operator_message,queue_delivery
from .service import thread_access,authorize_result
from .files import fingerprint


def reply_text(db,actor,delivery):
    if not delivery.assistant_turn_id:return None
    turn=locked(db,DialogueTurn,delivery.assistant_turn_id)
    thread=thread_access(db,actor,turn.thread_id)
    if not turn.source_message_id or delivery.message_id!=turn.source_message_id or delivery.actor_id!=thread.actor_id:
        raise DomainError('reply_source','Assistant reply source differs',403)
    source=operator_message(db,actor,turn.source_message_id,ChannelConfig.from_env())
    if delivery.recipient!=source.group_id or delivery.session!=source.session:
        raise DomainError('reply_source','Assistant reply destination differs',403)
    if db.get(Job,turn.job_id).status!='completed' or not turn.result or fingerprint(turn.result)!=delivery.assistant_result_hash:
        raise DomainError('stale_result','Assistant result changed',409)
    for item in turn.result.get('tools',[]):authorize_result(db,actor,thread,item['tool'],item['result'])
    from .worker import deterministic_reply
    return deterministic_reply(turn.result.get('tools',[]))['text'][:8000]


def queue_reply(db,actor,turn,source,config):
    digest=fingerprint(turn.result)
    # Validate before queue_delivery can create a durable row/command.
    candidate=Delivery(actor_id=actor.id,message_id=source.id,session=config.assistant_session,recipient=source.group_id,text='',assistant_turn_id=turn.id,assistant_result_hash=digest)
    reply_text(db,actor,candidate)
    result=queue_delivery(db,actor,{'message_id':source.id,'session':config.assistant_session,'recipient':source.group_id,'text':'助手回复','idempotency_key':'assistant-reply:'+turn.id})
    delivery=locked(db,Delivery,result['id'])
    delivery.assistant_turn_id=turn.id;delivery.assistant_result_hash=digest;delivery.text=''
    return result
