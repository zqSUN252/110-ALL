"""One immutable ownership boundary for source-dependent work and context."""
from datetime import timedelta
from app.common import DomainError,require
from app.models import ChannelMessage,utcnow

BOUNDARY=('instance','session','generation','group_id','warehouse_id','scope')


def same_boundary(left,right):
    return all(getattr(left,key)==getattr(right,key) for key in BOUNDARY)


def accepted_source(message,config):
    return bool(config.instance and config.generation and config.since>0
        and message.instance==config.instance and message.generation==config.generation
        and message.session in (config.capture_session,config.assistant_session)
        and config.groups.get(message.group_id)==message.warehouse_id and message.scope==config.scope
        and config.since<=message.sent_at.timestamp() and message.sent_at<=utcnow()+timedelta(minutes=5))


def require_source(actor,message,config,capability):
    require(actor,capability,message.warehouse_id)
    if not accepted_source(message,config):raise DomainError('source_out_of_scope','Source instance/generation/session/chat/warehouse/scope/time is no longer accepted',403)


def context_authorized(db,actor,message,context,config=None):
    require(actor,'channels.read',message.warehouse_id)
    for linked in context.get('related_messages',[]):
        source=db.get(ChannelMessage,linked.get('id'))
        if not source or not same_boundary(source,message):raise DomainError('context_scope','Stored context has a different source ownership boundary',403)
        require(actor,'channels.read',source.warehouse_id)
        if config:require_source(actor,source,config,'channels.read')
