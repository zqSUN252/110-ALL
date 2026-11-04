"""Preserved summary/echo configuration; no additional recognition calls."""
from sqlalchemy import select
from datetime import datetime,timezone
from app.common import DomainError,require,check_version
from app.models import ChannelAutomation,ChannelAutomationEvent,ChannelMessage,EvidenceImage,RecognitionJob,ConnectionEvent,Connection,Delivery
from app.finance.service import locked,record
from app.dialogue.commands import command
from .service import ChannelConfig,queue_delivery
from .source import require_source
from .worker import actor_now


def authorize(db,actor,p):
    require(actor,'channels.manage',p.get('warehouse_id'))
    if p.get('id'):
        row=locked(db,ChannelAutomation,p['id']);require(actor,'channels.manage',row.warehouse_id)
        if row.actor_id!=actor.id:raise DomainError('forbidden','Configuration belongs to another operator',403)


@command('channel.automation.configure','id expected_version kind warehouse_id scope source_group destination paused',authorize)
def configure(db,actor,p):
    config=ChannelConfig.from_env()
    if p['kind'] not in ('summary','echo') or p.get('scope') not in ('sample','live'):raise DomainError('automation_kind','Invalid automation scope or kind')
    if p['source_group']==p['destination'] or p['destination'] not in config.recipients or config.groups.get(p['source_group'])!=p['warehouse_id'] or config.scope!=p['scope']:raise DomainError('send_scope','Distinct allowed source and destination required',403)
    values={k:p[k] for k in ('kind','warehouse_id','scope','source_group','destination','paused')}
    if p.get('id'):
        row=locked(db,ChannelAutomation,p['id']);check_version(row,p.get('expected_version'))
        for k,v in values.items():setattr(row,k,v)
        row.version+=1
    else:
        if p.get('expected_version')!=0:raise DomainError('stale_version','Use zero to create',409)
        row=ChannelAutomation(actor_id=actor.id,**values);db.add(row)
    row.boundary={'instance':config.instance,'generation':config.generation,'since':config.since,'capture_session':config.capture_session,'assistant_session':config.assistant_session}
    db.flush();return record(row)


def allowed(db,actor,row,msg,settings,config):
    require(actor,'channels.manage',row.warehouse_id);require(actor,'channels.send',row.warehouse_id)
    require_source(actor,msg,config,'channels.read')
    if not settings.collection_enabled or not settings.summaries_enabled or row.paused:raise DomainError('automation_paused','Summary/echo is paused',409)
    if row.source_group!=msg.group_id or row.warehouse_id!=msg.warehouse_id or row.scope!=msg.scope or row.destination==row.source_group or row.destination not in config.recipients:raise DomainError('send_scope','Automation source/target changed',403)
    if row.boundary!={'instance':config.instance,'generation':config.generation,'since':config.since,'capture_session':config.capture_session,'assistant_session':config.assistant_session}:raise DomainError('source_scope','Automation deployment changed',403)


def delivery_guard(db,actor,item,settings,config):
    event=db.scalar(select(ChannelAutomationEvent).where(ChannelAutomationEvent.delivery_id==item.id))
    if event:
        row=locked(db,ChannelAutomation,event.automation_id);msg=locked(db,ChannelMessage,event.message_id)
        allowed(db,actor,row,msg,settings,config)
        if item.recipient!=row.destination or item.actor_id!=row.actor_id:raise DomainError('send_scope','Automation actor/target changed',403)
    connection_event=db.scalar(select(ConnectionEvent).where(ConnectionEvent.delivery_id==item.id))
    if connection_event:
        from app.connections.service import current
        row=locked(db,Connection,connection_event.connection_id)
        require(actor,'connections.manage')
        if not current(row,config) or row.alert_actor_id!=actor.id or row.alert_recipient!=item.recipient:raise DomainError('send_scope','Connection alert target changed',403)
        if (connection_event.kind=='offline' and row.status=='WORKING') or (connection_event.kind=='recovered' and row.status!='WORKING'):raise DomainError('alert_superseded','Connection alert no longer describes current state',409)


def automation_once(factory,settings,config=None):
    config=config or ChannelConfig.from_env()
    if not settings.collection_enabled or not settings.summaries_enabled:return {'status':'automation_paused','called':False}
    with factory() as db,db.begin():
        db.info['settings']=settings
        for row in db.scalars(select(ChannelAutomation).where(ChannelAutomation.paused.is_(False)).with_for_update(skip_locked=True)):
            try:actor=actor_now(db,row.actor_id)
            except DomainError:continue
            for msg in db.scalars(select(ChannelMessage).where(ChannelMessage.group_id==row.source_group,ChannelMessage.origin_state=='human',ChannelMessage.instance==config.instance,ChannelMessage.generation==config.generation,ChannelMessage.warehouse_id==row.warehouse_id,ChannelMessage.scope==row.scope,ChannelMessage.sent_at>=datetime.fromtimestamp(config.since,timezone.utc)).order_by(ChannelMessage.created_at)):
                if db.scalar(select(ChannelAutomationEvent.id).where(ChannelAutomationEvent.automation_id==row.id,ChannelAutomationEvent.message_id==msg.id)):continue
                try:allowed(db,actor,row,msg,settings,config)
                except DomainError:continue
                image=db.scalar(select(EvidenceImage).where(EvidenceImage.message_id==msg.id))
                whole=db.scalar(select(RecognitionJob).where(RecognitionJob.image_id==image.id,RecognitionJob.stage=='whole',RecognitionJob.state=='succeeded').order_by(RecognitionJob.created_at).limit(1)) if image else None
                if row.kind=='summary' and not whole:continue
                event=ChannelAutomationEvent(automation_id=row.id,message_id=msg.id,recognition_job_id=whole.id if whole else None,state='paused');db.add(event);db.flush()
                if not settings.sending_enabled:return {'status':'sending_disabled','called':False}
                if row.kind=='summary':
                    data=whole.result or {};fields=data.get('fields') or {};body='图片候选摘要（非到账确认）\n'+'\n'.join(str(k)+': '+str(v) for k,v in fields.items())
                else:body='原消息回显\n'+(msg.body or '')
                result=queue_delivery(db,actor,{'session':config.assistant_session,'recipient':row.destination,'text':body[:8000],'idempotency_key':'automation:'+event.id})
                event.delivery_id=result['id'];event.state='queued';return {'status':'queued','called':False}
        return {'status':'idle','called':False}
