from typing import Literal
from fastapi import APIRouter,Depends,Request
from fastapi.responses import Response
from pydantic import Field,ValidationError
from sqlalchemy import select
from app.api.dependencies import Database,get_actor,mutate
from app.common import DomainError,require,check_version,run_command,json_value
from app.models import DialogueThread,DialogueTurn,DialogueFile,Connection,ConnectionOperation,Attachment,ChannelAutomation,ChannelAutomationEvent,Delivery,DeliveryReceipt,Job,utcnow
from app.finance.service import locked,record
from app.api.finance import command_payload
from app.dialogue import service
from app.dialogue.inputs import Input,ThreadInput,TurnInput,FocusInput,ProposalInput,TOOLS
from app.dialogue.tools import dispatch
from app.dialogue.files import download,authorize_file
from app.connections import service as connections
from app.channels import automation
from app.attachments import read_file

router=APIRouter(prefix='/api')


class ConnectionInput(Input):
    expected_version:int=Field(ge=0)
    role:Literal['main','assistant','capture']
    session:str=Field(min_length=1,max_length=100)
    instance:str=Field(min_length=1,max_length=100)
    generation:str=Field(min_length=1,max_length=100)
    recovery_enabled:bool=False
    alert_actor_id:str|None=None
    alert_recipient:str|None=None


class OperationInput(Input):
    expected_version:int=Field(ge=1)
    kind:Literal['inspect','qr','connect','recover','diagnose']


class AutomationInput(Input):
    id:str|None=None
    expected_version:int=Field(ge=0)
    kind:Literal['summary','echo']
    warehouse_id:str
    scope:Literal['sample','live']
    source_group:str
    destination:str
    paused:bool=True


class ToolInput(Input):
    expected_version:int=Field(ge=1)
    turn_id:str
    name:Literal['query','case','compare','propose','bank_preview','file','export','source_assessment','notification']
    arguments:dict


class OriginInput(Input):
    expected_version:int=Field(ge=1)
    decision:Literal['human','bot']
    reason:str=Field(min_length=1,max_length=2000)


class SendFileInput(Input):
    expected_version:int=Field(ge=1)
    recipient:str=Field(min_length=1,max_length=250)


def mutation(path,model,handler):
    if '{id}' in path:
        def endpoint(id:str,payload,request:Request,db:Database,actor=Depends(mutate)):
            return handler(db,actor,{**command_payload(payload),'id':id,'idempotency_key':request.headers.get('Idempotency-Key')})
    else:
        def endpoint(payload,request:Request,db:Database,actor=Depends(mutate)):
            return handler(db,actor,{**command_payload(payload),'idempotency_key':request.headers.get('Idempotency-Key')})
    endpoint.__annotations__['payload']=model;endpoint.__name__=handler.__module__.replace('.','_')+'_'+handler.__name__
    router.add_api_route(path,endpoint,methods=['POST'])


for path,model,handler in [('/assistant/threads',ThreadInput,service.create_thread),('/assistant/threads/{id}/turns',TurnInput,service.submit_turn),('/assistant/threads/{id}/focus',FocusInput,service.set_focus),('/assistant/proposals/{id}/confirm',ProposalInput,service.confirm),('/connections',ConnectionInput,connections.configure),('/connections/{id}/operations',OperationInput,connections.request_operation),('/channels/automations',AutomationInput,automation.configure)]:mutation(path,model,handler)
from app.channels.origin import resolve_origin
mutation('/channels/messages/{id}/origin',OriginInput,resolve_origin)
from app.dialogue.files import send_file
mutation('/assistant/files/{id}/send',SendFileInput,send_file)


@router.get('/assistant/threads')
def threads(db:Database,actor=Depends(get_actor)):
    require(actor,'assistant.read')
    q=select(DialogueThread).where(DialogueThread.actor_id==actor.id)
    if '*' not in actor.capabilities:q=q.where(DialogueThread.warehouse_id.in_(actor.warehouse_ids))
    return [record(r) for r in db.scalars(q.order_by(DialogueThread.updated_at.desc()).limit(100))]


@router.get('/assistant/threads/{id}')
def thread(id:str,db:Database,actor=Depends(get_actor)):return service.get_thread(db,actor,id)


@router.post('/assistant/threads/{id}/tools')
def tool(id:str,payload:ToolInput,request:Request,db:Database,actor=Depends(mutate)):
    from app.dialogue.commands import domain_guard
    domain_guard(db)
    thread=service.thread_access(db,actor,id,'assistant.act');turn=locked(db,DialogueTurn,payload.turn_id)
    if turn.thread_id!=thread.id:raise DomainError('source_scope','Turn belongs to another thread',403)
    try:args=TOOLS[payload.name].model_validate(payload.arguments)
    except ValidationError:raise DomainError('invalid_tool_arguments','Strict tool schema failed',422) from None
    def apply():
        check_version(thread,payload.expected_version)
        result=json_value(dispatch(db,actor,thread,turn,payload.name,args));thread.version+=1
        from app.common import audit
        audit(db,actor,'assistant.tool','dialogue_thread',thread.id,None,{'tool':payload.name,'version':thread.version})
        return result|{'thread_version':thread.version}
    result=run_command(db,actor,'assistant.tool.http',request.headers.get('Idempotency-Key'),command_payload(payload)|{'thread_id':id},apply)
    service.authorize_result(db,actor,thread,payload.name,result)
    return result


@router.get('/assistant/files/{id}')
def file(id:str,db:Database,actor=Depends(get_actor)):
    content,mime=download(db,actor,id)
    return Response(content,media_type=mime,headers={'Content-Disposition':'attachment','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'"})


@router.get('/assistant/files/{id}/status')
def file_status(id:str,db:Database,actor=Depends(get_actor)):
    f=locked(db,DialogueFile,id);thread=service.thread_access(db,actor,f.thread_id);authorize_file(db,actor,thread,f)
    j=db.scalar(select(Job).where(Job.idempotency_key=='assistant.file:'+id))
    return {'id':id,'version':f.version,'kind':f.kind,'ready':bool(f.attachment_id) and f.kind!='bank_preview','status':j.status if j else 'linked','error_code':j.error_code if j else None,'preview':f.sources.get('preview')}


@router.get('/connections')
def connection_list(db:Database,actor=Depends(get_actor)):return connections.view(db,actor)


@router.get('/connections/{id}/operations')
def operations(id:str,db:Database,actor=Depends(get_actor)):
    require(actor,'connections.read');locked(db,Connection,id)
    return [record(r) for r in db.scalars(select(ConnectionOperation).where(ConnectionOperation.connection_id==id).order_by(ConnectionOperation.created_at.desc()).limit(100))]


@router.get('/connections/{id}/qr')
def qr(id:str,db:Database,actor=Depends(get_actor)):
    require(actor,'connections.manage');row=locked(db,Connection,id)
    from app.channels.service import ChannelConfig
    if not connections.current(row,ChannelConfig.from_env()):raise DomainError('qr_unavailable','Connection configuration changed',409)
    if row.status!='SCAN_QR_CODE' or not row.qr_attachment_id or not row.qr_expires_at or row.qr_expires_at<=utcnow():raise DomainError('qr_unavailable','No current QR',409)
    a=locked(db,Attachment,row.qr_attachment_id)
    if a.owner_type!='connection' or a.owner_id!=id:raise DomainError('qr_unavailable','QR owner differs',409)
    return Response(read_file(db.info['settings'],a),media_type='image/png',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.get('/channels/automations')
def automations(db:Database,actor=Depends(get_actor)):
    require(actor,'channels.read');q=select(ChannelAutomation).where(ChannelAutomation.actor_id==actor.id)
    if '*' not in actor.capabilities:q=q.where(ChannelAutomation.warehouse_id.in_(actor.warehouse_ids))
    return [record(r)|{'history':[record(h) for h in db.scalars(select(ChannelAutomationEvent).where(ChannelAutomationEvent.automation_id==r.id).order_by(ChannelAutomationEvent.created_at.desc()).limit(100))]} for r in db.scalars(q)]


@router.get('/channels/deliveries/{id}')
def delivery(id:str,db:Database,actor=Depends(get_actor)):
    require(actor,'channels.read');d=locked(db,Delivery,id)
    if actor.id!=d.actor_id:raise DomainError('forbidden','Delivery belongs to another operator',403)
    if d.message_id:
        from app.models import ChannelMessage
        require(actor,'channels.read',locked(db,ChannelMessage,d.message_id).warehouse_id)
    return record(d)|{'receipts':[record(r) for r in db.scalars(select(DeliveryReceipt).where(DeliveryReceipt.delivery_id==id))],'group_all_received':False}


@router.get('/channels/messages')
def channel_messages(db:Database,state:str|None=None,after:str|None=None,limit:int=50,actor=Depends(get_actor)):
    from app.models import ChannelMessage
    require(actor,'channels.read');q=select(ChannelMessage)
    if '*' not in actor.capabilities:q=q.where(ChannelMessage.warehouse_id.in_(actor.warehouse_ids))
    if state:q=q.where(ChannelMessage.state==state)
    if after:q=q.where(ChannelMessage.id>after)
    rows=list(db.scalars(q.order_by(ChannelMessage.id).limit(min(max(limit,1),200))))
    return {'items':[record(r) for r in rows],'next':rows[-1].id if rows else None}


@router.get('/channels/receipt-observations')
def receipt_observations(db:Database,actor=Depends(get_actor)):
    from app.models import ChannelReceiptObservation
    # Raw unknown provider payloads are diagnostics for connection administrators.
    require(actor,'connections.manage')
    return [record(r) for r in db.scalars(select(ChannelReceiptObservation).order_by(ChannelReceiptObservation.created_at.desc()).limit(100))]
