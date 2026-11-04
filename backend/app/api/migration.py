from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from app.api.dependencies import Database,get_actor,mutate
from app.common import require,DomainError
from app.models import MigrationBatch,MigrationIssue,MigrationFile,Attachment
from app.migration import service
from app.finance.service import record
from app.attachments import read_file

router=APIRouter(prefix='/api')


@router.post('/migration/sources')
async def upload_source(request:Request,db:Database,actor=Depends(mutate)):
    require(actor,'*')
    from app.migration.inputs import receive_archive
    return await receive_archive(request,db,actor)


@router.get('/migration/sources/{identity}/plan')
def source_plan(identity:str,dataset:str,db:Database,actor=Depends(get_actor)):
    require(actor,'*')
    from app.migration.inputs import read_source
    parsed,_=read_source(db,identity)
    return service.plan_source(parsed,dataset,'supplied_snapshot')


class SourceApply(BaseModel):
    model_config=ConfigDict(extra='forbid')
    dataset:str=Field(min_length=1,max_length=120)
    plan_hash:str=Field(pattern='^[0-9a-f]{64}$')


@router.post('/migration/sources/{identity}/apply')
def source_apply(identity:str,payload:SourceApply,request:Request,db:Database,actor=Depends(mutate)):
    require(actor,'*')
    from app.models import Job
    from app.common import run_command
    def queue():
        job=Job(kind='migration_apply',payload={'actor_id':actor.id,'source_id':identity,**payload.model_dump()},idempotency_key='migration-apply:'+request.headers['Idempotency-Key'])
        db.add(job);db.flush();return {'id':job.id,'state':job.status}
    return run_command(db,actor,'migration.queue_apply',request.headers.get('Idempotency-Key'),{'source_id':identity,**payload.model_dump()},queue)


@router.get('/migration/jobs/{identity}')
def job_status(identity:str,db:Database,actor=Depends(get_actor)):
    require(actor,'*')
    from app.models import Job
    row=db.get(Job,identity)
    if not row or row.kind not in ('migration_apply','migration_backup'):raise DomainError('not_found','Migration job unavailable',404)
    return {'id':row.id,'state':row.status,'error_code':row.error_code,'result':row.payload.get('result')}


@router.get('/public/profile')
def public_profile(db:Database):
    from app.models import AppSetting
    row=db.scalar(select(AppSetting).where(AppSetting.key=='documents.profile'))
    return {k:v for k,v in (row.value if row else {}).items() if k in ('public_hotline','public_openshipment')}


@router.get('/legacy/{kind}')
def listing(kind:str,db:Database,actor=Depends(get_actor),limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),query:str=Query('',max_length=200)):
    return service.legacy_list(db,actor,kind,limit,offset,query)


@router.get('/legacy/{kind}/{identity}')
def detail(kind:str,identity:str,db:Database,actor=Depends(get_actor)):
    return service.legacy_detail(db,actor,kind,identity)


class BillResolution(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int=Field(ge=1)
    party_id:str
    warehouse_id:str
    source:str=Field(min_length=1,max_length=2000)


class ShipmentResolution(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int=Field(ge=1)
    warehouse_id:str
    physical_reference:str|None=None
    uploaded_on:str
    source:str=Field(min_length=1,max_length=2000)


class PartyResolution(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int=Field(ge=1)
    warehouse_id:str
    source:str=Field(min_length=1,max_length=2000)


@router.post('/legacy/parties/{identity}/resolve')
def resolve_party(identity:str,payload:PartyResolution,request:Request,db:Database,actor=Depends(mutate)):
    return service.resolve_party(db,actor,identity,payload.model_dump()|{'idempotency_key':request.headers.get('Idempotency-Key')})


class ChargeResolution(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int=Field(ge=1)
    charge_components:list[dict]=Field(min_length=1,max_length=100)
    source:str=Field(min_length=1,max_length=2000)


class BankCoverage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    bill_id:str
    expected_version:int=Field(ge=1)
    invoice_version:int=Field(ge=1)
    amount_ngn:str
    source:str=Field(min_length=1,max_length=2000)


class BankEvidenceInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    claim_id:str
    claim_version:int=Field(ge=1)
    case_version:int=Field(ge=1)
    entry_version:int=Field(ge=1)
    account_version:int=Field(ge=1)
    control_owner_party_id:str
    members:list[BankCoverage]=Field(min_length=1,max_length=200)
    reason:str=Field(min_length=1,max_length=2000)
    source:str=Field(min_length=1,max_length=2000)
    cross_party:dict|None=None


class BankEvidenceConfirm(BankEvidenceInput):
    preview:dict


@router.post('/legacy/bank-evidence/preview')
def bank_evidence_preview(payload:BankEvidenceInput,db:Database,actor=Depends(mutate)):
    from app.migration.bank_evidence import preview
    return preview(db,actor,payload.model_dump())


@router.post('/legacy/bank-evidence/confirm')
def bank_evidence_confirm(payload:BankEvidenceConfirm,request:Request,db:Database,actor=Depends(mutate)):
    from app.migration.bank_evidence import bind
    return bind(db,actor,payload.model_dump()|{'idempotency_key':request.headers.get('Idempotency-Key')})


@router.get('/migration/bank-evidence/{identity}')
def bank_evidence_detail(identity:str,db:Database,actor=Depends(get_actor)):
    from app.migration.bank_evidence import detail
    return detail(db,actor,identity)


@router.post('/legacy/shipments/{identity}/resolve')
def resolve_ship(identity:str,payload:ShipmentResolution,request:Request,db:Database,actor=Depends(mutate)):
    return service.resolve_shipment(db,actor,identity,payload.model_dump()|{'idempotency_key':request.headers.get('Idempotency-Key')})


@router.post('/legacy/tickets/{identity}/charge-basis')
def resolve_basis(identity:str,payload:ChargeResolution,request:Request,db:Database,actor=Depends(mutate)):
    return service.resolve_charge_basis(db,actor,identity,payload.model_dump()|{'idempotency_key':request.headers.get('Idempotency-Key')})


@router.post('/legacy/bills/{identity}/resolve')
def resolve(identity:str,payload:BillResolution,request:Request,db:Database,actor=Depends(mutate)):
    return service.resolve_bill(db,actor,identity,payload.model_dump()|{'idempotency_key':request.headers.get('Idempotency-Key')})


@router.get('/migration/batches')
def batches(db:Database,actor=Depends(get_actor)):
    require(actor,'*')
    return [{'id':b.id,'version':b.version,'sha256':b.sha256,'source_version':b.source_version,'state':b.state,'controls':b.controls} for b in db.scalars(select(MigrationBatch).order_by(MigrationBatch.created_at.desc()).limit(100))]


@router.get('/migration/batches/{identity}/issues')
def issues(identity:str,db:Database,actor=Depends(get_actor),offset:int=Query(0,ge=0)):
    require(actor,'*')
    return [record(i) for i in db.scalars(select(MigrationIssue).where(MigrationIssue.batch_id==identity).order_by(MigrationIssue.id).offset(offset).limit(200))]


@router.get('/migration/batches/{identity}/sources')
def archived_sources(identity:str,db:Database,actor=Depends(get_actor),offset:int=Query(0,ge=0)):
    require(actor,'*')
    from app.models import LegacyArchive
    return [record(i) for i in db.scalars(select(LegacyArchive).where(LegacyArchive.batch_id==identity).order_by(LegacyArchive.id).offset(offset).limit(100))]


@router.get('/migration/files/{identity}')
def file(identity:str,db:Database,actor=Depends(get_actor)):
    service.legacy_access(actor,True);require(actor,'attachments.read')
    link=db.get(MigrationFile,identity)
    if not link:raise DomainError('not_found','Source attachment unavailable',404)
    attachment=db.get(Attachment,link.attachment_id)
    return Response(read_file(db.info['settings'],attachment),media_type=attachment.media_type,headers={'Cache-Control':'no-store','Content-Disposition':'attachment','X-Content-Type-Options':'nosniff'})


@router.post('/migration/backups')
def queue_backup(request:Request,db:Database,actor=Depends(mutate)):
    from app.migration.worker import enqueue_backup
    key=request.headers.get('Idempotency-Key')
    if not key or len(key)>120:raise DomainError('idempotency_required','Provide Idempotency-Key')
    return enqueue_backup(db,actor,key)
