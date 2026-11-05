"""Actual provider verification; suffix tracking never creates a customer session."""
from datetime import timedelta
from secrets import token_urlsafe,randbelow
from uuid import uuid4
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
import httpx
from sqlalchemy import select,func
from app.common import DomainError,Actor
from app.models import CustomerIdentity,CustomerSession,PhoneChallenge,User,Ticket,Receipt,PackingDocument,utcnow
from app.api.dependencies import token_hash
from app.tracking.service import phone,source_phone
from app.documents.projection import packing_projection


def provider_available(settings):
    return bool(settings.sending_enabled and settings.phone_delivery_enabled and settings.phone_provider_url and settings.phone_provider_token)


def request_challenge(db,settings,number,transport=None):
    if not provider_available(settings):raise DomainError('provider_disabled','Phone ownership delivery provider is disabled',503)
    if not phone(number):raise DomainError('phone','International recipient phone required')
    code=f'{randbelow(1000000):06d}'
    row=PhoneChallenge(phone=number,code_hash=PasswordHasher().hash(code),expires_at=utcnow()+timedelta(minutes=5))
    db.add(row);db.flush()
    if not settings.phone_provider_url.startswith('https://'):raise DomainError('provider_config','HTTPS delivery endpoint required',503)
    try:
        with httpx.Client(transport=transport,timeout=10,follow_redirects=False) as client:
            response=client.post(settings.phone_provider_url,headers={'Authorization':'Bearer '+settings.phone_provider_token},json={'recipient':number,'code':code,'reference':row.id,'purpose':'phone_ownership'})
        if response.status_code!=200 or not isinstance(response.json().get('delivery_reference'),str):row.status='uncertain';return {'challenge_id':row.id,'status':'delivery_unconfirmed'}
        row.status='sent';row.provider_reference=response.json()['delivery_reference'][:200]
        return {'challenge_id':row.id,'status':'sent','expires_at':row.expires_at.isoformat()}
    except (httpx.HTTPError,ValueError):
        row.status='uncertain';return {'challenge_id':row.id,'status':'delivery_unconfirmed'}


def verify(db,identity,code):
    row=db.scalar(select(PhoneChallenge).where(PhoneChallenge.id==identity).with_for_update())
    if not row:return None
    row.attempts+=1
    if row.attempts>5 or row.consumed or row.status!='sent' or row.expires_at<=utcnow():return None
    try:PasswordHasher().verify(row.code_hash,str(code))
    except VerificationError:return None
    row.consumed=True
    c=db.scalar(select(CustomerIdentity).where(CustomerIdentity.phone==row.phone).with_for_update())
    if not c:
        u=User(username='customer-'+uuid4().hex,password_hash=PasswordHasher().hash(token_urlsafe(48)));db.add(u);db.flush()
        c=CustomerIdentity(phone=row.phone,user_id=u.id,verified_at=utcnow());db.add(c);db.flush()
    if not c.active:return None
    import hmac,hashlib
    c.verified_at=utcnow();token=token_urlsafe(32);csrf=hmac.new(token.encode(),b'mcstar-customer-csrf-v1',hashlib.sha256).hexdigest()
    db.add(CustomerSession(customer_id=c.id,token_hash=token_hash(token),csrf_hash=token_hash(csrf),expires_at=utcnow()+timedelta(hours=12)))
    return {'customer':c,'token':token,'csrf':csrf}


def session(db,request,mutation=False):
    token=request.cookies.get('mcstar_customer','')
    s=db.scalar(select(CustomerSession).where(CustomerSession.token_hash==token_hash(token),CustomerSession.expires_at>utcnow(),CustomerSession.revoked_at.is_(None))) if token else None
    c=db.get(CustomerIdentity,s.customer_id) if s else None
    if not c or not c.active:raise DomainError('unauthenticated','Verified customer session required',401)
    if mutation and token_hash(request.headers.get('X-CSRF-Token',''))!=s.csrf_hash:raise DomainError('csrf','Valid CSRF token required',403)
    return c


def owns(db,c,obj):
    if not c.active or source_phone(obj)!=c.phone or obj.state!='active':return False
    if not isinstance(obj,Ticket):return True
    if db.get(PackingDocument,obj.document_id).state!='active':return False
    if obj.invoice_id:
        for other in db.scalars(select(Ticket).where(Ticket.invoice_id==obj.invoice_id,Ticket.state=='active')):
            if source_phone(other)!=c.phone or db.get(PackingDocument,other.document_id).state!='active':return False
    return True


def require_owned(db,c,objects):
    if not objects or any(not owns(db,c,o) for o in objects):raise DomainError('not_found','Customer document unavailable',404)


def documents(db,c,limit=50,offset=0):
    if not 1<=limit<=200 or offset<0:raise DomainError('pagination','Use page size 1..200')
    ticket_query=select(Ticket).join(PackingDocument,Ticket.document_id==PackingDocument.id).where(Ticket.phone_confirmed.is_(True),Ticket.phone_normalized==c.phone,Ticket.state=='active',PackingDocument.state=='active')
    from sqlalchemy.orm import aliased
    from sqlalchemy import or_
    other=aliased(Ticket);other_doc=aliased(PackingDocument)
    hidden=select(other.id).join(other_doc,other_doc.id==other.document_id).where(other.invoice_id==Ticket.invoice_id,other.state=='active',or_(other.phone_confirmed.is_(False),other.phone_normalized.is_(None),other.phone_normalized!=c.phone,other_doc.state!='active')).exists()
    ticket_query=ticket_query.where(~hidden)
    ticket_count=db.scalar(select(func.count()).select_from(ticket_query.subquery()))
    tickets=list(db.scalars(ticket_query.order_by(Ticket.id).limit(limit).offset(offset))) if offset<ticket_count else []
    receipts=list(db.scalars(select(Receipt).where(Receipt.customer_phone==c.phone,Receipt.state=='active').order_by(Receipt.id).limit(limit-len(tickets)).offset(max(0,offset-ticket_count)))) if len(tickets)<limit else []
    data=packing_projection(db,tickets+receipts)
    # Formal own-customer view intentionally includes commodity/fees, unlike last4 tracking.
    return {'rows':[{k:v for k,v in r.items() if k!='warehouse_confirmation'} for r in data['rows']],'limit':limit,'offset':offset}


def actor(c):return Actor(c.user_id,{'documents.read','documents.export','attachments.write'},set())
