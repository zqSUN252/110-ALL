"""One leased extraction job. Explicit invocation; no startup hooks or network in DB transactions."""
from datetime import timedelta
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from types import SimpleNamespace
import httpx
from PIL import Image
from sqlalchemy import select, or_
from app.models import RecognitionJob, EvidenceImage, EvidenceCase, Attachment, User, Grant, WarehouseScope, utcnow
from app.common import Actor, DomainError, require, audit
from app.attachments import read_file
from app.finance.service import locked
from app.bank.service import decision
from .recognition import ModelConfig, VisionClient, RecognitionError
from .image_prep import upright, classify_layout, build_details
from .service import FIELDS, cache_key, enqueue, set_fields


async def run_once(factory,settings,config=None,transport=None):
    from app.migration.operations import worker_gate
    worker_gate(factory,settings)
    config=config or ModelConfig.from_env('multimodal')
    if not settings.model_calls_enabled or not config.active:
        return {'status':'provider_unavailable','called':False}
    token=str(uuid4()); now=utcnow()
    with factory() as db,db.begin():
        job=db.scalar(select(RecognitionJob).where(RecognitionJob.available_at<=now,
            or_(RecognitionJob.state.in_(['pending','retry']), (RecognitionJob.state=='running') & (RecognitionJob.lease_until<now)))
            .order_by(RecognitionJob.available_at,RecognitionJob.id).with_for_update(skip_locked=True).limit(1))
        if not job: return {'status':'idle','called':False}
        image=db.get(EvidenceImage,job.image_id); case=db.get(EvidenceCase,image.case_id); attachment=db.get(Attachment,image.attachment_id)
        user=db.get(User,job.actor_id)
        who=Actor(job.actor_id,set(db.scalars(select(Grant.capability).where(Grant.user_id==job.actor_id))),set(db.scalars(select(WarehouseScope.warehouse_id).where(WarehouseScope.user_id==job.actor_id))))
        try:
            if not user or not user.active: raise DomainError('forbidden','Inactive worker requester',403)
            require(who,'evidence.recognize',case.warehouse_id)
            if image.message_id:
                from app.models import ChannelMessage
                from app.channels.service import ChannelConfig
                from app.channels.source import require_source,context_authorized
                message=db.get(ChannelMessage,image.message_id)
                channel_config=ChannelConfig.from_env()
                require_source(who,message,channel_config,'channels.read')
                context_authorized(db,who,message,image.context,channel_config)
        except DomainError:
            job.state='blocked'; job.error_code='permission_revoked'; return {'status':'blocked','called':False}
        # Profile mismatch means new configuration requires an explicit new request.
        if cache_key(image,attachment,config,job.case_version,job.stage)!=job.cache_key:
            job.state='blocked'; job.error_code='profile_changed'; return {'status':'blocked','called':False}
        if case.version!=job.case_version:
            job.state='stale'; return {'status':'stale','called':False}
        job.state='running'; job.lease_token=token; job.lease_until=now+timedelta(minutes=15); job.attempts+=1; job.version+=1
        identity=job.id; stage=job.stage; context=image.context; content=read_file(settings,attachment); sha=attachment.sha256
        previous=db.scalar(select(RecognitionJob).where(RecognitionJob.image_id==image.id,RecognitionJob.stage=='whole',RecognitionJob.state=='succeeded').order_by(RecognitionJob.created_at.desc()).limit(1))
        prior=previous.result if previous else {}
    result=None; error=None
    try:
        with TemporaryDirectory(dir=settings.private_root) as directory:
            original=Path(directory)/'original'; original.write_bytes(content); details=[]
            if stage=='zoom':
                with Image.open(original) as source:
                    prepared=upright(source)
                    layout=classify_layout(prior.get('raw_text',''),prepared.size,prior.get('source_issues',[]))
                    views=build_details(prepared,layout,prior.get('source_issues',[]),prior.get('field_boxes'))
                    for n,view in enumerate(views['details']):
                        path=Path(directory)/f'detail-{n}.png'; view['image'].save(path)
                        details.append((path,hashlib.sha256(path.read_bytes()).hexdigest()))
            async with httpx.AsyncClient(transport=transport,trust_env=False) as client:
                extracted=await VisionClient(client).extract(config,original,sha,details,context)
            # Vision client returns parsed result and metadata; inspect its typed tuple.
            result=extracted[1] if isinstance(extracted,tuple) else extracted
    except RecognitionError as exc: error=str(exc)
    except (OSError,ValueError,TypeError): error='image_processing_failed'
    with factory() as db,db.begin():
        job=locked(db,RecognitionJob,identity)
        if job.lease_token!=token or job.state!='running': return {'status':'lease_lost','called':True}
        job.lease_until=None; job.lease_token=None; job.version+=1
        if error:
            job.error_code=error; job.state='retry' if job.attempts<3 else 'failed'; job.available_at=utcnow()+timedelta(minutes=job.attempts*5)
            return {'id':identity,'status':job.state,'error_code':error,'called':True}
        image=db.get(EvidenceImage,job.image_id); case=locked(db,EvidenceCase,image.case_id)
        job.result=result; job.state='succeeded'
        if case.version!=job.case_version:
            job.state='stale'; return {'id':identity,'status':'stale','called':True}
        # Multi-image cases may contain separate transactions or inconsistent readings.
        # Keep each full result; only a sole image can propose fields on the case.
        images=list(db.scalars(select(EvidenceImage.id).where(EvidenceImage.case_id==case.id)))
        fields={key:value for key,value in result.get('fields',{}).items() if key in FIELDS and key not in case.manual_fields and value is not None
            and result.get('field_origins',{}).get(key,{}).get('kind')!='context_inferred'}
        # Model proposals never silently mutate a versioned, human-reviewed case.
        # A transient projection selects the zoom stage; the UI explicitly reviews
        # the immutable per-image result before committing normalized case fields.
        projection=SimpleNamespace(**{c.name:getattr(case,c.name) for c in case.__table__.columns})
        if len(images)==1: set_fields(projection,fields)
        matched=decision(db,projection).get('state')=='matched'
        if stage=='whole' and not matched:
            enqueue(db,who,case,image,config,stage='zoom')
        audit(db,who,'evidence.recognized','evidence_case',case.id,None,{'job_id':job.id,'stage':stage,'matched_candidate':matched,'posted':False})
        return {'id':identity,'status':job.state,'stage':stage,'called':True,'posted':False}
