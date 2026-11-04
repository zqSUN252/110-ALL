import hashlib
import json
from sqlalchemy import select
from app.common import DomainError, require, check_version, json_value
from app.models import (Attachment, EvidenceCase, EvidenceImage, EvidenceReview, RecognitionJob, BankAccount,
    Payment, ChannelMessage, utcnow)
from app.finance.service import command, access, locked, record, scope, decimal, day
from app.bank.service import attachment_for_actor, freeze_case
from app.finance.access import account_access,party_member
from .recognition import ModelConfig
from .recognition_context import message_context

FIELDS={'amount','currency','transaction_date','payer_name','transaction_reference','recipient_account','bank_name','business_reference'}


def set_fields(case,fields):
    if not isinstance(fields,dict) or set(fields)-FIELDS:
        raise DomainError('invalid_fields','Unsupported evidence fields')
    for key,value in fields.items():
        if value is not None:
            if key=='amount': value=decimal(value,positive=True)
            elif key=='transaction_date': value=day(value)
            elif key=='currency' and value not in ('NGN','USD','CNY'): raise DomainError('invalid_currency','Unsupported currency')
            elif not isinstance(value,str) or len(value)>500: raise DomainError('invalid_fields','Evidence text invalid')
        setattr(case,key,value)


def context_for_message(db,message,actor=None):
    from datetime import timedelta
    from app.channels.service import ChannelConfig
    from app.channels.source import accepted_source
    config=ChannelConfig.from_env()
    linked=list(db.scalars(select(ChannelMessage).where(ChannelMessage.instance==message.instance,ChannelMessage.session==message.session,
        ChannelMessage.group_id==message.group_id,ChannelMessage.scope==message.scope,ChannelMessage.warehouse_id==message.warehouse_id,
        ChannelMessage.generation==message.generation,ChannelMessage.sent_at>=message.sent_at-timedelta(hours=1),
        ChannelMessage.sent_at<=message.sent_at+timedelta(minutes=15),ChannelMessage.body.is_not(None)).order_by(ChannelMessage.sent_at,ChannelMessage.id).limit(24)))
    linked=[m for m in linked if accepted_source(m,config)]
    if actor:
        for m in linked:require(actor,'channels.read',m.warehouse_id)
    related=[{'id':m.id,'message_id':m.source_id,'body':m.body[:8000],'relation':'explicit_reply' if m.source_id==message.reply_to or m.reply_to==message.source_id else 'nearby_unconfirmed'} for m in linked if m.id!=message.id]
    return message_context({'id':message.id,'message_id':message.source_id,'sent_at':message.sent_at.timestamp(),'group_id':message.group_id,
        'sender':message.sender_id,'reply_to_id':message.reply_to,'caption':message.body},related)


def add_images(db,actor,case,rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=100: raise DomainError('images_required','Provide 1 to 100 evidence images')
    known=set(db.scalars(select(Attachment.sha256).join(EvidenceImage,EvidenceImage.attachment_id==Attachment.id).where(EvidenceImage.case_id==case.id)))
    basis_changed=False
    for row in rows:
        if set(row)-{'attachment_id','source','message_id'}: raise DomainError('invalid_image','Unsupported evidence source fields')
        attachment=attachment_for_actor(db,actor,row['attachment_id'])
        basis_changed=basis_changed or attachment.sha256 not in known
        known.add(attachment.sha256)
        if attachment.media_type not in ('image/png','image/jpeg','image/webp'):
            raise DomainError('image_required','Evidence attachment must be an image')
        context={}; message_id=row.get('message_id')
        if message_id:
            message=access(db,actor,ChannelMessage,message_id,'channels.read')
            from app.channels.service import ChannelConfig
            from app.channels.source import require_source
            require_source(actor,message,ChannelConfig.from_env(),'channels.read')
            if message.scope!=case.scope or message.warehouse_id!=case.warehouse_id: raise DomainError('scope_mismatch','Message and evidence scope differ')
            context=context_for_message(db,message,actor)
        db.add(EvidenceImage(case_id=case.id,attachment_id=attachment.id,source=row.get('source','manual_upload'),message_id=message_id,context=context))
    return basis_changed


@command('evidence.create','warehouse_id scope account_id payment_id payment_version fields images')
def create_case(db,actor,p):
    require(actor,'evidence.write',p['warehouse_id']); mode=scope(p.get('scope','live'))
    if p.get('account_id'):
        account=access(db,actor,BankAccount,p['account_id'],'bank.read')
        account_access(db,actor,account,'bank.read',p['warehouse_id'])
        if account.scope!=mode: raise DomainError('scope_mismatch','Bank and case scope differ')
    if p.get('payment_id'):
        payment=access(db,actor,Payment,p['payment_id'],'finance.post'); check_version(payment,p.get('payment_version'))
        require(actor,'finance.post',p['warehouse_id'])
        if payment.scope!=mode: raise DomainError('scope_mismatch','Payment and case scope differ')
    case=EvidenceCase(warehouse_id=p['warehouse_id'],scope=mode,account_id=p.get('account_id'),payment_id=p.get('payment_id'))
    set_fields(case,p.get('fields',{})); case.manual_fields=list(p.get('fields',{}))
    db.add(case); db.flush(); add_images(db,actor,case,p['images']); db.flush()
    return get_case(db,actor,case.id)


@command('evidence.attach','id expected_version images')
def attach_images(db,actor,p):
    case=access(db,actor,EvidenceCase,p['id'],'evidence.write'); check_version(case,p.get('expected_version'))
    changed=add_images(db,actor,case,p['images'])
    if changed:freeze_case(db,case,'evidence_added')
    case.version+=1; db.flush()
    return get_case(db,actor,case.id)


@command('evidence.review','id expected_version fields action reason')
def review_case(db,actor,p):
    case=access(db,actor,EvidenceCase,p['id'],'evidence.review'); check_version(case,p.get('expected_version'))
    action=p.get('action','correct')
    if action not in ('correct','defer','retract'): raise DomainError('invalid_action','Unsupported evidence action')
    fields=p.get('fields',{}); set_fields(case,fields)
    case.manual_fields=sorted(set(case.manual_fields)|set(fields))
    freeze_case(db,case,action)
    db.add(EvidenceReview(case_id=case.id,actor_id=actor.id,action=action,previous_version=case.version,fields=json_value(fields),reason=p.get('reason')))
    case.version+=1; db.flush(); return get_case(db,actor,case.id)


def get_case(db,actor,identity):
    case=access(db,actor,EvidenceCase,identity,'evidence.read')
    from app.finance.authorization import case_read
    case_read(db,actor,case)
    return record(case)|{'images':[record(i) for i in db.scalars(select(EvidenceImage).where(EvidenceImage.case_id==case.id).order_by(EvidenceImage.created_at,EvidenceImage.id))],
        'reviews':[record(i) for i in db.scalars(select(EvidenceReview).where(EvidenceReview.case_id==case.id).order_by(EvidenceReview.created_at))]}


@command('evidence.bind_account','id expected_version account_id reason')
def bind_account(db,actor,p):
    case=access(db,actor,EvidenceCase,p['id'],'evidence.review');check_version(case,p.get('expected_version'))
    if not isinstance(p.get('reason'),str) or not p['reason'].strip():raise DomainError('reason_required','Account binding needs a source-backed reason')
    target=p.get('account_id')
    if target:
        account=access(db,actor,BankAccount,target,'bank.read');account_access(db,actor,account,'bank.read',case.warehouse_id)
        if account.scope!=case.scope:raise DomainError('scope_mismatch','Bank account and evidence scope differ')
    previous=case.account_id
    if previous!=target:
        freeze_case(db,case,'account_changed');case.account_id=target
    db.add(EvidenceReview(case_id=case.id,actor_id=actor.id,action='bind_account',previous_version=case.version,fields={'previous_account_id':previous,'account_id':target},reason=p['reason']))
    case.version+=1;db.flush();return get_case(db,actor,case.id)


def cache_key(image,attachment,config,case_version,stage):
    return hashlib.sha256(json.dumps([image.id,attachment.sha256,config.profile,image.context,case_version,stage],sort_keys=True,separators=(',',':')).encode()).hexdigest()


def enqueue(db,actor,case,image,config,stage='whole'):
    attachment=db.get(Attachment,image.attachment_id); key=cache_key(image,attachment,config,case.version,stage)
    previous=db.scalar(select(RecognitionJob).where(RecognitionJob.cache_key==key))
    if previous: return previous
    job=RecognitionJob(image_id=image.id,actor_id=actor.id,case_version=case.version,stage=stage,cache_key=key,available_at=utcnow())
    db.add(job); db.flush(); return job


@command('evidence.recognize','id expected_version')
def recognize(db,actor,p):
    case=access(db,actor,EvidenceCase,p['id'],'evidence.recognize'); check_version(case,p.get('expected_version'))
    config=ModelConfig.from_env('multimodal')
    freeze_case(db,case,'recognition_pending'); case.version+=1
    jobs=[record(enqueue(db,actor,case,i,config)) for i in db.scalars(select(EvidenceImage).where(EvidenceImage.case_id==case.id))]
    available=db.info['settings'].model_calls_enabled and config.active
    return {'id':case.id,'version':case.version,'jobs':jobs,'provider_available':available,'status':'queued' if available else 'provider_unavailable'}
