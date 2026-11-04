"""Authorized domain endpoints. Dirty fields are validated by shared command schemas."""
from fastapi import APIRouter,Depends,Request,Body
from app.api.dependencies import Database,get_actor,mutate
from app.common import DomainError
from app.cargo import service as cargo
from app.pricing import service as pricing
from app.shipments import service as shipments
from app.packing import service as packing,imports
from app.warehouses import analytics
from app.warehouses import masters,exports
from app.cargo import translation
from .logistics_inputs import MODELS

router=APIRouter(prefix='/api')


def add_command(path,fn,method='POST'):
    if '{id}' in path:
        def endpoint(id:str,request:Request,db:Database,payload:dict=Body(...),actor=Depends(mutate)):
            if hasattr(payload,'model_dump'):payload=payload.model_dump(exclude_unset=True)
            if 'id' in payload or 'idempotency_key' in payload:raise DomainError('path_identity','ID and idempotency key are supplied by path/header',422)
            return fn(db,actor,dict(payload,id=id,idempotency_key=request.headers.get('Idempotency-Key')))
    else:
        def endpoint(request:Request,db:Database,payload:dict=Body(...),actor=Depends(mutate)):
            if hasattr(payload,'model_dump'):payload=payload.model_dump(exclude_unset=True)
            if 'idempotency_key' in payload:raise DomainError('header_key','Use Idempotency-Key header',422)
            return fn(db,actor,dict(payload,idempotency_key=request.headers.get('Idempotency-Key')))
    endpoint.__name__=fn.__name__+'_'+method.lower()
    endpoint.__annotations__['payload']=MODELS.get(fn.__name__,dict)
    router.add_api_route(path,endpoint,methods=[method])


for path,fn,method in [
    ('/receipts',cargo.create_receipt,'POST'),('/receipts/{id}',cargo.update_receipt,'PATCH'),
    ('/receipts/{id}/repack',cargo.repack,'POST'),('/receipts/{id}/review',cargo.review_receipt,'POST'),
    ('/receipts/{id}/state',cargo.receipt_state,'POST'),('/cargo/dispatch',cargo.dispatch,'POST'),
    ('/receipts/{id}/quote',pricing.quote_receipt,'POST'),('/price-cards',pricing.create_card,'POST'),('/price-cards/{id}',pricing.update_card,'PATCH'),
    ('/shipments',shipments.create_shipment,'POST'),('/shipments/{id}',shipments.update_shipment,'PATCH'),
    ('/packing/documents',packing.create_document,'POST'),('/tickets',packing.create_ticket,'POST'),('/tickets/{id}',packing.update_ticket,'PATCH'),
    ('/tickets/{id}/arrive',packing.arrive,'POST'),('/tickets/{id}/attachments',packing.attach_ticket,'POST'),('/tickets/{id}/review',packing.review_ticket,'POST'),
    ('/tickets/{id}/void',packing.void_ticket,'POST'),
    ('/packing/documents/{id}/split',packing.split_document,'POST'),('/packing/documents/{id}/void',packing.void_document,'POST'),('/packing/documents/{id}/recover',packing.recover_document,'POST'),
    ('/cargo/imports/preview',imports.preview_import,'POST'),('/cargo/imports/{id}/commit',imports.commit_import,'POST'),('/packing/documents/{id}/replace',imports.replace_document,'POST'),
    ('/parties/{id}',masters.update_party,'PATCH'),('/warehouses/{id}',masters.update_warehouse,'PATCH'),
    ('/receipts/{id}/merge',cargo.merge_receipts,'POST'),('/receipts/{id}/attachments',cargo.attach_receipt,'POST'),
    ('/receipts/{id}/translation',translation.request_translation,'POST'),('/translations/{id}/accept',translation.accept_translation,'POST'),
    ('/cargo/exports',exports.export_receipts,'POST'),('/f6/analysis/export',exports.export_inbound,'POST'),
]:add_command(path,fn,method)


@router.get('/receipts')
def receipts(db:Database,limit:int=50,offset:int=0,receipt:str|None=None,client:str|None=None,item:str|None=None,destination:str|None=None,transport:str|None=None,warehouse_id:str|None=None,state:str|None=None,actor=Depends(get_actor)):
    return cargo.list_receipts(db,actor,limit=limit,offset=offset,receipt=receipt,client=client,item=item,destination=destination,transport=transport,warehouse_id=warehouse_id,state=state)


@router.get('/receipts/numbering-gaps')
def gaps(warehouse_id:str,series:str,db:Database,actor=Depends(get_actor)):return cargo.numbering_gaps(db,actor,warehouse_id,series)


@router.get('/receipts/{id}')
def receipt(id:str,db:Database,actor=Depends(get_actor)):return cargo.get_receipt(db,actor,id)


@router.get('/price-cards')
def cards(db:Database,on:str|None=None,destination:str|None=None,limit:int=50,offset:int=0,actor=Depends(get_actor)):return pricing.list_cards(db,actor,on,destination,limit,offset)


@router.get('/shipments')
def shipment_list(db:Database,limit:int=50,offset:int=0,transport:str|None=None,state:str|None=None,destination:str|None=None,warehouse_id:str|None=None,actor=Depends(get_actor)):
    return shipments.list_shipments(db,actor,limit,offset,transport,state,destination,warehouse_id)


@router.get('/shipments/{id}')
def shipment(id:str,db:Database,actor=Depends(get_actor)):return shipments.get_shipment(db,actor,id)


@router.get('/packing/documents')
def document_list(db:Database,limit:int=50,offset:int=0,shipment_id:str|None=None,state:str|None=None,actor=Depends(get_actor)):return packing.list_documents(db,actor,limit,offset,shipment_id,state)


@router.get('/packing/documents/{id}')
def document(id:str,db:Database,actor=Depends(get_actor)):return packing.get_document(db,actor,id)


@router.get('/packing/documents/{id}/history')
def document_history(id:str,db:Database,actor=Depends(get_actor)):return packing.get_document_history(db,actor,id)


@router.get('/packing/documents/{id}/groups')
def groups(id:str,db:Database,actor=Depends(get_actor)):return packing.document_groups(db,actor,id)


@router.get('/packing/documents/{id}/void-preview')
def document_void_preview(id:str,db:Database,actor=Depends(get_actor)):return packing.void_preview(db,actor,id)


@router.get('/packing/documents/{id}/recover-preview')
def document_recover_preview(id:str,db:Database,actor=Depends(get_actor)):return packing.recover_preview(db,actor,id)


@router.post('/packing/documents/{id}/replacement-preview')
def replacement_preview(id:str,db:Database,payload:dict=Body(...),actor=Depends(mutate)):
    from app.cargo.commands import fields
    fields(payload,'batch_id mapping excluded_rows');return imports.replacement_preview(db,actor,id,payload['batch_id'],payload['mapping'],payload.get('excluded_rows'))


@router.get('/tickets')
def tickets(db:Database,limit:int=50,offset:int=0,receipt:str|None=None,client:str|None=None,item:str|None=None,destination:str|None=None,transport:str|None=None,state:str|None=None,document_id:str|None=None,actor=Depends(get_actor)):
    return packing.list_tickets(db,actor,limit,offset,receipt,client,item,destination,transport,state,document_id)


@router.get('/tickets/{id}')
def ticket(id:str,db:Database,actor=Depends(get_actor)):return packing.get_ticket(db,actor,id)


@router.post('/tickets/{id}/change-preview')
def ticket_preview(id:str,db:Database,payload:dict=Body(...),actor=Depends(mutate)):return packing.ticket_change_preview(db,actor,id,payload)


@router.get('/tickets/{id}/void-preview')
def single_void_preview(id:str,db:Database,actor=Depends(get_actor)):return packing.ticket_void_preview(db,actor,id)


@router.get('/cargo/imports/{id}')
def preview(id:str,db:Database,actor=Depends(get_actor)):return imports.get_preview(db,actor,id)


@router.get('/f6')
def f6(db:Database,warehouse_id:str|None=None,query:str|None=None,sort:str='receipt',limit:int=50,offset:int=0,actor=Depends(get_actor)):return imports.list_f6(db,actor,warehouse_id,query,sort,limit,offset)


@router.get('/f6/analysis')
def analysis(db:Database,on:str|None=None,warehouse_id:str|None=None,salesperson:str|None=None,actor=Depends(get_actor)):return analytics.inbound(db,actor,on,warehouse_id,salesperson)


@router.get('/parties/{id}')
def party(id:str,db:Database,actor=Depends(get_actor)):return masters.get_party(db,actor,id)


@router.get('/warehouses/{id}')
def warehouse(id:str,db:Database,actor=Depends(get_actor)):return masters.get_warehouse(db,actor,id)


@router.get('/translations/{id}')
def translation_candidate(id:str,db:Database,actor=Depends(get_actor)):return translation.get_translation(db,actor,id)


def download(db,a):
    from fastapi.responses import Response
    from app.attachments import read_file
    return Response(read_file(db.info['settings'],a),media_type=a.media_type,headers={'Content-Disposition':'attachment','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.get('/receipts/{id}/attachments/{attachment_id}')
def receipt_file(id:str,attachment_id:str,db:Database,actor=Depends(get_actor)):
    from app.models import Attachment
    from app.finance.service import locked
    cargo.get_receipt(db,actor,id);a=locked(db,Attachment,attachment_id)
    if a.owner_type!='receipt' or a.owner_id!=id:raise DomainError('attachment_scope','Attachment not related to this receipt',403)
    return download(db,a)


@router.get('/tickets/{id}/attachments/{attachment_id}')
def ticket_file(id:str,attachment_id:str,db:Database,actor=Depends(get_actor)):
    from sqlalchemy import select
    from app.models import Attachment,TicketAttachment
    from app.finance.service import locked
    packing.get_ticket(db,actor,id)
    if not db.scalar(select(TicketAttachment.id).where(TicketAttachment.ticket_id==id,TicketAttachment.attachment_id==attachment_id)):raise DomainError('attachment_scope','Attachment not related to this ticket',403)
    return download(db,locked(db,Attachment,attachment_id))


@router.get('/packing/documents/{id}/original/{batch_id}')
def original_file(id:str,batch_id:str,db:Database,actor=Depends(get_actor)):
    from sqlalchemy import select
    from app.models import DocumentRevision,ImportBatch,Attachment
    from app.finance.service import locked
    packing.get_document(db,actor,id)
    if not db.scalar(select(DocumentRevision.id).where(DocumentRevision.document_id==id,DocumentRevision.batch_id==batch_id)):raise DomainError('source_scope','Original file not related to document',403)
    b=locked(db,ImportBatch,batch_id);return download(db,locked(db,Attachment,b.attachment_id))


@router.get('/cargo/exports/{id}')
def exported_file(id:str,db:Database,actor=Depends(get_actor)):
    from app.common import require
    from app.models import Attachment
    from app.finance.service import locked
    a=locked(db,Attachment,id)
    if a.owner_type!='cargo_export':raise DomainError('export_scope','Not a cargo export',403)
    require(actor,'cargo.export',a.owner_id);require(actor,'cargo.read',a.owner_id)
    return download(db,a)


@router.get('/cargo/export-jobs/{id}')
def export_job(id:str,db:Database,actor=Depends(get_actor)):
    from app.common import require
    from app.models import Job
    from app.finance.service import locked,record
    j=locked(db,Job,id)
    if j.kind!='cargo_export':raise DomainError('job_scope','Not a cargo export job',403)
    require(actor,'cargo.export',j.payload['warehouse_id']);return record(j)
