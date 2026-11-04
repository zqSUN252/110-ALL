from sqlalchemy import select,text
from app.common import DomainError
from app.models import Delivery,DeliveryReceipt,utcnow
from app.models import ChannelReceiptObservation
import hashlib,json


def observe_receipt(db,event,config):
    session=event.get('session')
    if not isinstance(session,str) or len(session)>120 or session not in (config.assistant_session,config.capture_session,config.main_session):return {'accepted':False,'reason':'session_out_of_scope'}
    payload=event.get('payload')
    if not isinstance(payload,dict):raise DomainError('invalid_receipt','Receipt payload must be an object')
    digest=hashlib.sha256(json.dumps(event,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'receipt-observation:'+config.instance+session+digest})
    prior=db.scalar(select(ChannelReceiptObservation).where(ChannelReceiptObservation.instance==config.instance,ChannelReceiptObservation.session==session,ChannelReceiptObservation.body_hash==digest))
    if prior:return {'accepted':True,'id':prior.id,'state':prior.state,'duplicate':True}
    observation=ChannelReceiptObservation(instance=config.instance,session=session,body_hash=digest,event_type=event['event'],payload=payload,state='held');db.add(observation);db.flush()
    try:
        result=receipt_event(db,event,config)
        if result.get('accepted'):observation.state='parsed';observation.receipt_id=result['id']
    except DomainError:pass
    return {'accepted':True,'id':observation.id,'state':observation.state,'duplicate':False}


def receipt_event(db,event,config):
    p=event.get('payload') or {};session=event.get('session');identity=p.get('id');chat=p.get('chatId') or p.get('to') or p.get('from');participant=p.get('participant') or ''
    ack=p.get('ack')
    if session not in (config.assistant_session,config.capture_session,config.main_session) or not isinstance(identity,str) or not isinstance(chat,str) or not isinstance(participant,str) or type(ack) is not int or ack not in (-1,0,1,2,3,4):raise DomainError('invalid_receipt','Trusted session, exact message and receipt fields required')
    if len(identity)>500 or len(chat)>250 or len(participant)>250:raise DomainError('invalid_receipt','Receipt identity too long')
    # Group delivered/read facts require an actual participant. Never infer all members received.
    if chat.endswith('@g.us') and ack>=2 and (not participant or participant==chat):return {'accepted':False,'reason':'participant_required'}
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'receipt:'+config.instance+session+identity})
    prior=db.scalar(select(DeliveryReceipt).where(DeliveryReceipt.instance==config.instance,DeliveryReceipt.session==session,DeliveryReceipt.source_id==identity,DeliveryReceipt.chat_id==chat,DeliveryReceipt.participant==participant,DeliveryReceipt.ack==ack))
    if prior:return {'accepted':True,'id':prior.id,'duplicate':True}
    receipt=DeliveryReceipt(instance=config.instance,session=session,source_id=identity,chat_id=chat,participant=participant,ack=ack,received_at=utcnow());db.add(receipt);db.flush()
    link_receipts(db,config.instance,session,identity)
    return {'accepted':True,'id':receipt.id,'duplicate':False}


def link_receipts(db,instance,session,identity):
    for item in db.scalars(select(Delivery).where(Delivery.session==session,Delivery.receipt==identity).with_for_update()):
        if not item.boundary or item.boundary.get('instance')!=instance:continue
        for receipt in db.scalars(select(DeliveryReceipt).where(DeliveryReceipt.instance==instance,DeliveryReceipt.session==session,DeliveryReceipt.source_id==identity,DeliveryReceipt.chat_id==item.recipient)):
            receipt.delivery_id=item.id
            # For groups the row is still provider-submitted; per-person facts are separate.
            if item.recipient.endswith('@g.us'):continue
            if receipt.ack>=3:item.state='read'
            elif receipt.ack==2 and item.state!='read':item.state='delivered'
            elif receipt.ack==1 and item.state in ('sending','uncertain','pending'):item.state='submitted'
        item.version+=1
