from fastapi import APIRouter,Depends,Request,Body,Response
from app.api.dependencies import Database,get_actor,mutate
from app.common import DomainError,require
from app.documents import release,notifications,exports,mbl
from app.tracking import service as tracking,customer
from .documents_inputs import MODELS
from app.documents.configuration import configure_profile

router=APIRouter(prefix='/api')


def add(path,fn,method='POST'):
    if '{id}' in path:
        def endpoint(id:str,request:Request,db:Database,payload:dict=Body(...),actor=Depends(mutate)):
            if hasattr(payload,'model_dump'):payload=payload.model_dump(exclude_unset=True)
            if 'id' in payload or 'idempotency_key' in payload:raise DomainError('path_identity','Use path identity and Idempotency-Key header',422)
            return fn(db,actor,dict(payload,id=id,idempotency_key=request.headers.get('Idempotency-Key')))
    else:
        def endpoint(request:Request,db:Database,payload:dict=Body(...),actor=Depends(mutate)):
            if hasattr(payload,'model_dump'):payload=payload.model_dump(exclude_unset=True)
            if 'idempotency_key' in payload:raise DomainError('header_key','Use Idempotency-Key header',422)
            return fn(db,actor,dict(payload,idempotency_key=request.headers.get('Idempotency-Key')))
    endpoint.__name__=fn.__name__;endpoint.__annotations__['payload']=MODELS.get(fn.__name__,dict);router.add_api_route(path,endpoint,methods=[method])


for path,fn in [('/release-orders',release.issue_order),('/physical-releases',release.physical_release),('/holds/remove',release.remove_holds),('/holds/{id}/cancel',release.cancel_removal),('/payment-registrations',release.register_payment),('/notifications/render',notifications.render),('/notifications/{id}/copy-events',notifications.copy_success),('/notification-copies/{id}/cancel',notifications.cancel_copy),('/tracking/nodes',tracking.set_node),('/exports',exports.queue_export)]:add(path,fn)
for path,fn in [('/mbl',mbl.save_mbl),('/document-templates',mbl.configure_template),('/mbl/{id}/exports',mbl.queue),('/mbl/{id}/attachments',mbl.attach)]:add(path,fn)
add('/documents/profile',configure_profile)


@router.get('/documents/profile')
def internal_profile(db:Database,actor=Depends(get_actor)):
    from sqlalchemy import select
    from app.models import AppSetting
    from app.finance.service import record
    require(actor,'documents.configure');require(actor,'documents.read')
    row=db.scalar(select(AppSetting).where(AppSetting.key=='documents.profile'))
    return record(row) if row else {'version':0,'value':{}}


@router.get('/mbl/{id}')
def mbl_record(id:str,db:Database,actor=Depends(get_actor)):return mbl.get(db,actor,id)


@router.get('/mbl/{id}/files/{attachment_id}')
def mbl_file(id:str,attachment_id:str,request:Request,db:Database,actor=Depends(get_actor)):return file_response(*mbl.original(db,request.app.state.settings,actor,id,attachment_id))


@router.get('/release-orders/{id}/eligibility')
def eligibility(id:str,db:Database,actor=Depends(get_actor)):return release.eligible_order(db,actor,id)


@router.get('/release-orders')
def orders(db:Database,limit:int=50,offset:int=0,actor=Depends(get_actor)):return release.list_orders(db,actor,limit,offset)


@router.get('/physical-releases/{id}/photos/{attachment_id}')
def pickup_photo(id:str,attachment_id:str,request:Request,db:Database,actor=Depends(get_actor)):return file_response(*release.photo_bytes(db,request.app.state.settings,id,attachment_id,actor=actor))


@router.get('/customer/physical-releases/{id}/photos/{attachment_id}')
def customer_photo(id:str,attachment_id:str,request:Request,db:Database):return file_response(*release.photo_bytes(db,request.app.state.settings,id,attachment_id,customer=customer.session(db,request)))


@router.get('/tickets/{id}/receivable-state')
def receivable(id:str,db:Database,actor=Depends(get_actor)):return release.receivable_state(db,actor,id)


@router.get('/tickets/{id}/holds')
def ticket_holds(id:str,db:Database,actor=Depends(get_actor)):
    from app.models import Ticket,Receipt
    from app.finance.service import locked
    t=locked(db,Ticket,id);require(actor,'release.read',t.warehouse_id)
    result={'ticket_id':id,'source_version':t.version,'ticket':release.hold_state(db,'ticket',id),'native':None}
    if t.native_receipt_id:
        native=locked(db,Receipt,t.native_receipt_id);require(actor,'release.read',native.warehouse_id)
        result['native']={'receipt_id':native.id,'source_version':native.version,**release.hold_state(db,'receipt',native.id)}
    return result


@router.get('/receipts/{id}/holds')
def receipt_holds(id:str,db:Database,actor=Depends(get_actor)):
    from app.models import Receipt
    from app.finance.service import locked
    r=locked(db,Receipt,id);require(actor,'release.read',r.warehouse_id)
    return {'receipt_id':r.id,'source_version':r.version,**release.hold_state(db,'receipt',r.id)}


@router.get('/tickets/{id}/notification-marks')
def notification_marks(id:str,db:Database,actor=Depends(get_actor)):
    from app.models import Ticket
    from app.finance.service import locked
    t=locked(db,Ticket,id);require(actor,'documents.read',t.warehouse_id)
    return notifications.marks(db,[id])[id]


@router.get('/tickets/{id}/timeline')
def timeline(id:str,db:Database,actor=Depends(get_actor)):return tracking.internal_timeline(db,actor,id)


@router.get('/tracking/warehouse-preview')
def warehouse_preview(target_type:str,target_id:str,destination_warehouse_id:str,db:Database,actor=Depends(get_actor)):return tracking.warehouse_preview(db,actor,target_type,target_id,destination_warehouse_id)


@router.get('/exports/{id}')
def export_status(id:str,db:Database,actor=Depends(get_actor)):return exports.status(db,id,actor)


def file_response(content,media):return Response(content,media_type=media,headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Disposition':'attachment'})


@router.get('/exports/{id}/file')
def export_file(id:str,request:Request,db:Database,actor=Depends(get_actor)):return file_response(*exports.download(db,request.app.state.settings,id,actor))


@router.get('/exports/{id}/clipboard')
def clipboard_image(id:str,request:Request,db:Database,actor=Depends(get_actor)):
    content,media=exports.download(db,request.app.state.settings,id,actor)
    if media!='image/png':raise DomainError('clipboard_format','Request a PNG clipboard artifact')
    return Response(content,media_type=media,headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.post('/customer/exports')
def customer_export(request:Request,db:Database,payload:dict=Body(...)):
    c=customer.session(db,request,True)
    if 'idempotency_key' in payload:raise DomainError('header_key','Use Idempotency-Key header',422)
    return exports.queue_customer(db,c,dict(payload,idempotency_key=request.headers.get('Idempotency-Key')))


@router.get('/customer/exports/{id}')
def customer_status(id:str,request:Request,db:Database):return exports.status(db,id,customer=customer.session(db,request))


@router.get('/customer/exports/{id}/file')
def customer_file(id:str,request:Request,db:Database):return file_response(*exports.download(db,request.app.state.settings,id,customer=customer.session(db,request)))


@router.get('/customer/exports/{id}/clipboard')
def customer_clipboard(id:str,request:Request,db:Database):
    content,media=exports.download(db,request.app.state.settings,id,customer=customer.session(db,request))
    if media!='image/png':raise DomainError('clipboard_format','Request a PNG clipboard artifact')
    return Response(content,media_type=media,headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.get('/documents/resources')
def resources(request:Request,actor=Depends(get_actor)):
    from app.documents.rendering import resources
    require(actor,'documents.read');return resources(request.app.state.settings)


@router.get('/notifications/{id}/image')
def notification_image(id:str,request:Request,db:Database,actor=Depends(get_actor)):
    from app.documents.access import authorize
    from app.models import Notification
    from app.finance.service import locked
    from app.documents.rendering import png
    authorize(db,actor,{'notification_id':id},'documents.notify');n=locked(db,Notification,id)
    notifications.current_generation(db,actor,n)
    return Response(png(request.app.state.settings,n.body.splitlines()),media_type='image/png',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
