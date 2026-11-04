from fastapi import APIRouter,Request,Body,Response
from fastapi.responses import JSONResponse
from app.api.dependencies import Database
from app.common import DomainError
from app.tracking import service,customer

router=APIRouter(prefix='/api')


def failure(code='tracking_unavailable',message='No matching authorized record',status=404):
    return JSONResponse({'error':{'code':code,'message':message}},status_code=status,headers={'Cache-Control':'private, no-store'})


@router.post('/tracking/query')
def query(request:Request,db:Database,payload:dict=Body(...)):
    if not service.rate_limit(db,request.client.host,'tracking'):return failure('rate_limited','Try later',429)
    if set(payload)-{'reference','last4','language'}:return failure()
    rows=service.public_query(db,payload.get('reference'),payload.get('last4'),payload.get('language','en'))
    return {'rows':rows} if rows else failure()


@router.post('/customer/verification')
def verification(request:Request,db:Database,payload:dict=Body(...)):
    if not service.rate_limit(db,request.client.host,'phone-request',5):return failure('rate_limited','Try later',429)
    try:return customer.request_challenge(db,request.app.state.settings,payload.get('phone'),getattr(request.app.state,'phone_transport',None))
    except DomainError as exc:return failure(exc.code,exc.message,exc.status)


@router.post('/customer/register')
def register(request:Request,db:Database,payload:dict=Body(...)):
    if not service.rate_limit(db,request.client.host,'phone-verify',15):return failure('rate_limited','Try later',429)
    if not customer.provider_available(request.app.state.settings):return failure('provider_disabled','Phone ownership delivery provider is disabled',503)
    result=customer.verify(db,payload.get('challenge_id'),payload.get('code'))
    if not result:return failure('verification_failed','Phone verification failed',400)
    response=JSONResponse({'verified':True,'csrf_token':result['csrf']},headers={'Cache-Control':'private, no-store'})
    response.set_cookie('mcstar_customer',result['token'],httponly=True,secure=request.app.state.settings.secure_cookies,samesite='strict',max_age=43200)
    return response


@router.get('/customer/documents')
def documents(request:Request,db:Database,limit:int=50,offset:int=0):
    return customer.documents(db,customer.session(db,request),limit,offset)


@router.post('/customer/logout')
def logout(request:Request,db:Database):
    from sqlalchemy import select
    from app.models import CustomerSession,utcnow
    from app.api.dependencies import token_hash
    from app.common import audit
    c=customer.session(db,request,True)
    s=db.scalar(select(CustomerSession).where(CustomerSession.token_hash==token_hash(request.cookies['mcstar_customer'])).with_for_update())
    s.revoked_at=utcnow();s.version+=1
    audit(db,customer.actor(c),'customer.logout','customer_session',s.id,None,{'revoked':True})
    response=Response(status_code=204);response.delete_cookie('mcstar_customer',secure=request.app.state.settings.secure_cookies,httponly=True,samesite='strict');return response


@router.get('/public/profile')
def public_profile(db:Database):
    from app.documents.projection import profile
    values=profile(db)
    return {k.removeprefix('public_'):v for k,v in values.items() if k.startswith('public_')}
