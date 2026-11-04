"""Typed internal APIs. All mutations use the function-scoped Database transaction."""
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.api.dependencies import Database, get_actor, mutate
from app.common import DomainError, require
from app.finance import service as finance
from app.finance.export import export_approved
from app.bank import service as bank
from app.bank.archive import export_archive
from app.evidence import service as evidence
from app.channels import service as channels
from app.models import Invoice, Payment, EvidenceCase, BankFile, BankAccount, Attachment, EvidenceImage, RecognitionJob,Party,Warehouse,PartyWarehouse,BankAccountWarehouse
from app.finance.access import visible_payments,account_access
from app.attachments import read_file

router=APIRouter(prefix='/api')


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Version(Input):
    expected_version:int=Field(ge=1)


class FX(Input):
    direction:Literal['NATIVE','NGN_PER_USD','NGN_PER_CNY','USD_CNY_CROSS']
    rate:str|None=None
    ngn_per_usd:str|None=None
    cny_per_usd:str|None=None


class WarehouseInput(Input):
    name:str=Field(min_length=1,max_length=200)
    address:str|None=None
    contact:str|None=None
    source:str|None=None


class PartyInput(Input):
    name:str=Field(min_length=1,max_length=200)
    warehouse_id:str
    classification:str|None=None
    phone:str|None=None
    address:str|None=None
    source:str|None=None


class InvoiceInput(Input):
    party_id:str
    warehouse_id:str
    scope:Literal['live','sample']='live'
    kind:Literal['AR','AP']='AR'
    reference:str=Field(min_length=1,max_length=200)
    amount_ngn:str
    charge_usd:str|None=None
    charge_ngn:str|None=None
    ngn_per_usd:str|None=None
    uploaded_on:str
    period:str|None=None
    source_key:str|None=None
    pricing:dict=Field(default_factory=dict)


class PaymentInput(Input):
    party_id:str
    warehouse_id:str
    scope:Literal['live','sample']='live'
    amount:str
    currency:Literal['NGN','USD','CNY']
    fx:FX|None=None
    paid_on:str|None=None
    source_key:str|None=None
    period:str|None=None


class AllocationInput(Input):
    invoice_id:str
    expected_version:int=Field(ge=1)
    amount_ngn:str


class BeneficiaryInput(Input):
    invoice_id:str
    party_id:str


class CrossPartyInput(Input):
    source:str=Field(min_length=1)
    beneficiaries:list[BeneficiaryInput]=Field(min_length=1,max_length=200)


class PostInput(Version):
    approval:Literal['manual','bank']='manual'
    allocations:list[AllocationInput]=Field(min_length=1,max_length=200)
    case_id:str|None=None
    case_version:int|None=None
    cross_party:CrossPartyInput|None=None


class AdjustInput(Version):
    amount_ngn:str
    reason:str=Field(min_length=1)
    pricing:dict|None=None
    preview:dict
    discount_total_ngn:str|None=None
    charge_usd:str|None=None
    charge_ngn:str|None=None
    ngn_per_usd:str|None=None


class DiscountInput(Version):
    amount_ngn:str
    reason:str|None=None
    waiver:bool=False


class ReverseInput(Version):
    reason:str=Field(min_length=1)
    confirmed:bool
    claim_resolution:Literal['disputed','released']='disputed'


class AttachBankInput(Version):
    case_id:str
    case_version:int=Field(ge=1)


class PayableInput(PaymentInput):
    period:str=Field(min_length=1)
    allocations:list[AllocationInput]|None=None


class OpeningInput(Input):
    party_id:str
    warehouse_id:str
    scope:Literal['live','sample']='live'
    amount_ngn:str
    kind:Literal['debt','credit']
    as_of:str
    source:str=Field(min_length=1)


class AccountInput(Input):
    warehouse_id:str
    scope:Literal['live','sample']='live'
    bank_name:str
    account_number:str
    currency:Literal['NGN','USD','CNY']


class ImportInput(Input):
    account_id:str
    attachment_id:str


class ConfirmImportInput(Version):
    account_version:int=Field(ge=1)
    sheets:list[str]=Field(min_length=1,max_length=40)
    date_order:Literal['auto','dmy','mdy']='auto'
    trusted_reference:bool=False


class FieldsInput(Input):
    amount:str|None=None
    currency:Literal['NGN','USD','CNY']|None=None
    transaction_date:str|None=None
    payer_name:str|None=None
    transaction_reference:str|None=None
    recipient_account:str|None=None
    bank_name:str|None=None
    business_reference:str|None=None


class ImageInput(Input):
    attachment_id:str
    source:str='manual_upload'
    message_id:str|None=None


class EvidenceInput(Input):
    warehouse_id:str
    scope:Literal['live','sample']='live'
    account_id:str|None=None
    payment_id:str|None=None
    payment_version:int|None=None
    fields:FieldsInput=Field(default_factory=FieldsInput)
    images:list[ImageInput]=Field(min_length=1,max_length=100)


class ImagesInput(Version):
    images:list[ImageInput]=Field(min_length=1,max_length=100)


class BindAccountInput(Version):
    account_id:str|None
    reason:str=Field(min_length=1)


class MembershipInput(Version):
    warehouse_id:str
    active:bool
    source:str=Field(min_length=1)


class ReviewInput(Version):
    fields:FieldsInput=Field(default_factory=FieldsInput)
    action:Literal['correct','defer','retract']='correct'
    reason:str|None=None


class BankReviewInput(Version):
    entry_id:str|None=None
    entry_version:int|None=None
    action:Literal['confirm','confirm_manual','defer','retract','release','resolve','resolve_manual']='confirm'
    reason:str|None=None


class ActionInput(Input):
    message_id:str
    action:Literal['payment.post','evidence.review','bank.review']
    payload:dict
    text:str=''


class ExecuteInput(Version):
    message_id:str


class DeliveryInput(Input):
    message_id:str|None=None
    session:str
    recipient:str
    text:str=Field(min_length=1,max_length=8000)


class ClaimSelection(Input):
    id:str
    expected_version:int=Field(ge=1)


class ArchiveInput(Input):
    warehouse_id:str
    template_attachment_id:str
    claims:list[ClaimSelection]=Field(min_length=1,max_length=5000)
    label:str=''


class ExportInput(Input):
    warehouse_id:str
    scope:Literal['live','sample']='live'
    on:str


def command_payload(model):
    # Defaults are retained, but explicit null is distinct from omitted optional
    # fields, recursively (especially manual evidence corrections).
    result={}
    for name in type(model).model_fields:
        value=getattr(model,name)
        if value is None and name not in model.model_fields_set:continue
        if isinstance(value,BaseModel):value=command_payload(value)
        elif isinstance(value,list):value=[command_payload(v) if isinstance(v,BaseModel) else v for v in value]
        result[name]=value
    return result


def mutation(path,model,handler):
    if '{id}' in path:
        def endpoint(id:str,payload,request:Request,db:Database,actor=Depends(mutate)):
            body=command_payload(payload)
            return handler(db,actor,{**body,'id':id,'idempotency_key':request.headers.get('Idempotency-Key')})
    else:
        def endpoint(payload,request:Request,db:Database,actor=Depends(mutate)):
            body=command_payload(payload)
            return handler(db,actor,{**body,'idempotency_key':request.headers.get('Idempotency-Key')})
    endpoint.__annotations__['payload']=model
    endpoint.__name__=handler.__module__.replace('.','_')+'_'+handler.__name__
    router.add_api_route(path,endpoint,methods=['POST'])


for path,model,handler in [
    ('/warehouses',WarehouseInput,finance.create_warehouse),('/parties',PartyInput,finance.create_party),
    ('/parties/{id}/warehouses',MembershipInput,finance.set_party_membership),
    ('/bank/accounts/{id}/warehouses',MembershipInput,bank.set_account_membership),
    ('/invoices',InvoiceInput,finance.create_invoice),('/invoices/{id}/adjust',AdjustInput,finance.adjust_invoice),
    ('/invoices/{id}/discounts',DiscountInput,finance.add_discount),('/payments',PaymentInput,finance.create_payment),
    ('/payments/{id}/post',PostInput,finance.post_payment),('/payments/{id}/reverse',ReverseInput,finance.reverse_payment),
    ('/payments/{id}/bank-evidence',AttachBankInput,finance.attach_bank_evidence),('/payable-payments',PayableInput,finance.pay_payable),
    ('/opening-balances',OpeningInput,finance.create_opening_balance),('/bank/accounts',AccountInput,bank.create_account),
    ('/bank/imports/preview',ImportInput,bank.import_preview),('/bank/imports/{id}/confirm',ConfirmImportInput,bank.confirm_import),
    ('/bank/cases/{id}/match',Version,bank.match_case),('/bank/cases/{id}/review',BankReviewInput,bank.review_case),
    ('/evidence',EvidenceInput,evidence.create_case),('/evidence/{id}/images',ImagesInput,evidence.attach_images),
    ('/evidence/{id}/review',ReviewInput,evidence.review_case),('/evidence/{id}/recognize',Version,evidence.recognize),
    ('/evidence/{id}/bank-account',BindAccountInput,evidence.bind_account),
    ('/channels/actions',ActionInput,channels.prepare_action),('/channels/actions/{id}/execute',ExecuteInput,channels.execute_action),
    ('/channels/deliveries',DeliveryInput,channels.queue_delivery),('/bank/archives',ArchiveInput,export_archive),
    ('/finance/exports/approved',ExportInput,export_approved)]: mutation(path,model,handler)


@router.get('/invoices/{id}')
def invoice(id:str,db:Database,actor=Depends(get_actor)): return finance.get_invoice(db,actor,id)


@router.get('/invoices/{id}/adjust-preview')
def adjust_preview(id:str,amount_ngn:str,db:Database,discount_total_ngn:str|None=None,actor=Depends(get_actor)):
    return finance.adjustment_preview(db,actor,id,amount_ngn,discount_total_ngn)


@router.get('/payments/{id}')
def payment(id:str,db:Database,actor=Depends(get_actor)): return finance.get_payment(db,actor,id)


@router.get('/evidence/{id}')
def case(id:str,db:Database,actor=Depends(get_actor)): return evidence.get_case(db,actor,id)


@router.get('/bank/imports/{id}')
def bank_import(id:str,db:Database,actor=Depends(get_actor)): return bank.get_import(db,actor,id)


@router.get('/finance/totals')
def totals(warehouse_id:str,db:Database,scope:str='live',on:str|None=None,actor=Depends(get_actor)):
    return finance.totals(db,actor,warehouse_id,scope,on)


@router.get('/payables/period')
def payable_period(warehouse_id:str,party_id:str,period:str,db:Database,scope:str='live',actor=Depends(get_actor)):
    return finance.payable_period(db,actor,warehouse_id,party_id,period,scope)


@router.get('/finance/records/{kind}')
def records(kind:str,warehouse_id:str,db:Database,scope:str='live',after:str|None=None,limit:int=50,party_id:str|None=None,actor=Depends(get_actor)):
    choices={'invoices':(Invoice,'finance.read'),'payments':(Payment,'finance.read'),'payables':(Invoice,'payables.read'),'payable-payments':(Payment,'payables.read'),'evidence':(EvidenceCase,'evidence.read'),'bank-accounts':(BankAccount,'bank.read')}
    if kind not in choices: raise DomainError('not_found','Unknown record collection',404)
    model,cap=choices[kind]; require(actor,cap,warehouse_id); finance.scope(scope)
    query=select(model).where(model.scope==scope)
    if model is BankAccount:
        query=query.join(BankAccountWarehouse,BankAccountWarehouse.account_id==BankAccount.id).where(BankAccountWarehouse.warehouse_id==warehouse_id,BankAccountWarehouse.active.is_(True))
    else:query=query.where(model.warehouse_id==warehouse_id)
    if model is Invoice:query=query.where(model.kind=='AP' if kind=='payables' else 'AR')
    if model is Payment:query=visible_payments(query.where(model.direction=='out' if kind=='payable-payments' else 'in'),actor)
    if party_id and model in (Invoice,Payment):query=query.where(model.party_id==party_id)
    if after: query=query.where(model.id>after)
    rows=list(db.scalars(query.order_by(model.id).limit(min(max(limit,1),200))))
    serialize=finance.invoice_json if model is Invoice else finance.payment_json if model is Payment else lambda db,obj:finance.record(obj)
    return {'items':[serialize(db,row) for row in rows],'next':rows[-1].id if rows else None}


@router.get('/parties')
def parties(warehouse_id:str,db:Database,after:str|None=None,limit:int=50,actor=Depends(get_actor)):
    require(actor,'parties.read',warehouse_id)
    query=select(Party).join(PartyWarehouse,PartyWarehouse.party_id==Party.id).where(PartyWarehouse.warehouse_id==warehouse_id,PartyWarehouse.active.is_(True))
    if after:query=query.where(Party.id>after)
    rows=list(db.scalars(query.order_by(Party.id).limit(min(max(limit,1),200))))
    return {'items':[finance.record(row) for row in rows],'next':rows[-1].id if rows else None}


@router.get('/warehouses')
def warehouses(db:Database,actor=Depends(get_actor)):
    require(actor,'warehouses.read')
    query=select(Warehouse)
    if '*' not in actor.capabilities:query=query.where(Warehouse.id.in_(actor.warehouse_ids))
    return [finance.record(row) for row in db.scalars(query.order_by(Warehouse.name).limit(200))]


@router.get('/evidence-jobs/{id}')
def evidence_job(id:str,db:Database,actor=Depends(get_actor)):
    job=finance.locked(db,RecognitionJob,id); image=db.get(EvidenceImage,job.image_id)
    from app.finance.authorization import case_read
    case_read(db,actor,finance.access(db,actor,EvidenceCase,image.case_id,'evidence.read'))
    return finance.record(job)


@router.get('/finance/files/{id}')
def domain_file(id:str,db:Database,actor=Depends(get_actor)):
    attachment=finance.locked(db,Attachment,id)
    case=db.scalar(select(EvidenceCase).join(EvidenceImage,EvidenceImage.case_id==EvidenceCase.id).where(EvidenceImage.attachment_id==id))
    file=db.scalar(select(BankFile).where(BankFile.attachment_id==id))
    if case: require(actor,'evidence.read',case.warehouse_id)
    elif file: account_access(db,actor,db.get(BankAccount,file.account_id),'bank.read')
    elif attachment.owner_type in ('bank_archive','finance_export'):
        from app.finance.authorization import export_file_access
        export_file_access(db,actor,attachment)
    else: raise DomainError('forbidden','No authorized domain attachment relation',403)
    return Response(read_file(db.info['settings'],attachment),media_type=attachment.media_type,
        headers={'Content-Disposition':'attachment','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.post('/webhooks/waha')
async def webhook(request:Request,db:Database):
    raw=bytearray()
    async for block in request.stream():
        if len(raw)+len(block)>1024*1024: raise DomainError('event_too_large','Webhook body exceeds limit',413)
        raw.extend(block)
    return channels.intake(db,request.app.state.settings,bytes(raw),request.headers.get('X-Webhook-Hmac',''),request.headers.get('X-Webhook-Hmac-Algorithm',''))
