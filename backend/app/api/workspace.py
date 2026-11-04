"""Purpose-specific workspace projections. Never expose arbitrary tables or paths."""
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, or_, func
from app.api.dependencies import Database, get_actor, mutate
from app.common import require, check_version, audit, run_command, DomainError
from app.models import (Announcement, Grant, WarehouseScope, AppSetting, Attachment,
    Party, Receipt, Ticket, Shipment, Invoice, BankEntry, BankAccount, MBLRecord,
    Warehouse, PackingDocument, Payment, EvidenceCase, LegacyBill, LegacyTicket,
    LegacyParty, LegacyShipment, BankClaim, BankAccountWarehouse, Allocation, Discount,
    LegacyBillApplication, PaymentRegistration, ReleaseOrder, ReleaseMember, PartyWarehouse)
from app.finance.service import record
from app.finance.access import account_access
from app.attachments import read_file

router = APIRouter(prefix='/api')


@router.get('/workspace/summary')
def workspace_summary(db:Database,actor=Depends(get_actor)):
    result={}
    for model,kind,cap in [(Receipt,'receipts','cargo.read'),(PackingDocument,'documents','packing.read'),(Ticket,'tickets','packing.read'),(Invoice,'invoices','finance.read'),(EvidenceCase,'evidence','evidence.read'),(ReleaseOrder,'release','release.read')]:
        if '*' not in actor.capabilities and cap not in actor.capabilities:continue
        statement=select(func.count()).select_from(model)
        if model is Invoice:statement=statement.where(Invoice.kind=='AR',Invoice.scope=='live')
        if hasattr(model,'warehouse_id') and '*' not in actor.capabilities:statement=statement.where(model.warehouse_id.in_(actor.warehouse_ids))
        # Release orders can span warehouses. The existing projection owns its
        # full membership authorization; do not advertise an unscoped count.
        if model is ReleaseOrder and '*' not in actor.capabilities:continue
        result[kind]=db.scalar(statement)
    if '*' in actor.capabilities or {'legacy.global_read','finance.read'}.issubset(actor.capabilities):
        result['legacy-bills']=db.scalar(select(func.count()).select_from(LegacyBill))
        result['legacy-tickets']=db.scalar(select(func.count()).select_from(LegacyTicket))
    return {'counts':result}


@router.get('/workspace/integrations')
def integration_flags(request: Request,actor=Depends(get_actor)):
    settings=request.app.state.settings
    return {name:getattr(settings,name) for name in ('collection_enabled','summaries_enabled','model_calls_enabled','sending_enabled','connection_control_enabled','phone_delivery_enabled','main_account_read_only')}


@router.get('/bank/accounts/{identity}')
def account_detail(identity: str,db: Database,actor=Depends(get_actor)):
    row=db.get(BankAccount,identity)
    if not row: raise DomainError('not_found','Bank account unavailable',404)
    account_access(db,actor,row,'bank.read')
    return record(row)


@router.get('/price-cards/{identity}')
def price_card(identity: str,db: Database,actor=Depends(get_actor)):
    from app.models import PriceCard
    require(actor,'pricing.read');row=db.get(PriceCard,identity)
    if not row:raise DomainError('not_found','Price card unavailable',404)
    return record(row)


@router.get('/workspace/finance')
def finance_list(db: Database, actor=Depends(get_actor), kind: str='AR', warehouse_id: str|None=None,
                 party_id: str|None=None, query: str=Query('',max_length=200), settlement: str|None=None, period: str|None=None, after: str|None=None, limit: int=Query(50,ge=1,le=200)):
    if kind not in ('AR','AP'): raise DomainError('invalid_kind','Select AR or AP',422)
    cap='finance.read' if kind=='AR' else 'payables.read';require(actor,cap,warehouse_id)
    allocations=select(Allocation.invoice_id,func.sum(Allocation.amount_ngn).label('allocated')).group_by(Allocation.invoice_id).subquery()
    discounts=select(Discount.invoice_id,func.sum(Discount.amount_ngn).label('discount')).group_by(Discount.invoice_id).subquery()
    histories=select(LegacyBill.native_id.label('invoice_id'),LegacyBillApplication.applied_received.label('historical')).join(LegacyBillApplication,LegacyBillApplication.bill_id==LegacyBill.id).subquery()
    allocated=func.coalesce(allocations.c.allocated,0);discount=func.coalesce(discounts.c.discount,0);historical=func.coalesce(histories.c.historical,0)
    statement=select(Invoice,Party.name.label('party_name'),allocated.label('allocated'),discount.label('discount'),historical.label('historical')).join(Party,Party.id==Invoice.party_id).outerjoin(allocations,allocations.c.invoice_id==Invoice.id).outerjoin(discounts,discounts.c.invoice_id==Invoice.id).outerjoin(histories,histories.c.invoice_id==Invoice.id).where(Invoice.kind==kind,Invoice.scope=='live')
    if warehouse_id:statement=statement.where(Invoice.warehouse_id==warehouse_id)
    if '*' not in actor.capabilities:statement=statement.where(Invoice.warehouse_id.in_(actor.warehouse_ids))
    if party_id:statement=statement.where(Invoice.party_id==party_id)
    if period:statement=statement.where(Invoice.period==period)
    if settlement:
        outstanding=Invoice.amount_ngn-allocated-discount-historical
        if settlement=='unpaid':statement=statement.where(outstanding>0)
        elif settlement=='partial':statement=statement.where(outstanding>0,allocated+historical>0)
        elif settlement=='settled':statement=statement.where(outstanding<=0)
        else:raise DomainError('invalid_settlement','Choose unpaid, partial or settled',422)
    if query:statement=statement.where(or_(Invoice.reference.icontains(query,autoescape=True),Party.name.icontains(query,autoescape=True),Party.phone.icontains(query,autoescape=True)))
    sub=statement.subquery()
    aggregate=db.execute(select(func.count(),func.coalesce(func.sum(sub.c.amount_ngn),0),func.coalesce(func.sum(sub.c.allocated),0),func.coalesce(func.sum(sub.c.discount),0),func.coalesce(func.sum(sub.c.historical),0)).select_from(sub)).one()
    if after:statement=statement.where(Invoice.id>after)
    page=list(db.execute(statement.order_by(Invoice.id).limit(limit+1)))
    result=[]
    from app.common import json_value
    for invoice,name,a,d,h in page[:limit]:
        tickets=list(db.scalars(select(Ticket).where(Ticket.invoice_id==invoice.id).order_by(Ticket.position,Ticket.id)))
        registered=bool(db.scalar(select(PaymentRegistration.id).where(PaymentRegistration.invoice_id==invoice.id,PaymentRegistration.active.is_(True)).limit(1))) or a>0 or h>0
        color=None
        if kind=='AR':
            from app.documents.release import _current
            orders=list(db.scalars(select(ReleaseOrder).join(ReleaseMember).join(Ticket,Ticket.id==ReleaseMember.ticket_id).where(Ticket.invoice_id==invoice.id).distinct()))
            valid=[o for o in orders if _current(db,o)[0]]
            color='yellow' if any(o.mode=='credit' for o in valid) or (a+h>0 and invoice.amount_ngn-a-d-h>0 and valid) else 'red' if not registered else None
        result.append(record(invoice)|json_value({'party_name':name,'allocated_ngn':a,'discount_ngn':d,'historical_received_ngn':h,'outstanding_ngn':invoice.amount_ngn-a-d-h,'color':color,
            'ticket_ids':[t.id for t in tickets],'destination':list(dict.fromkeys(t.destination for t in tickets if t.destination)),
            'customer_phone':list(dict.fromkeys(t.contact_raw for t in tickets if t.contact_raw)),
            'pieces':sum(t.pieces for t in tickets) if tickets and all(t.pieces is not None for t in tickets) else None,
            'cbm':sum(t.cbm for t in tickets) if tickets and all(t.cbm is not None for t in tickets) else None}))
    return {'items':result,'next':page[limit-1][0].id if len(page)>limit else None,'total':aggregate[0],
        'totals':json_value({'amount_ngn':aggregate[1],'allocated_ngn':aggregate[2],'discount_ngn':aggregate[3],'historical_received_ngn':aggregate[4],'outstanding_ngn':aggregate[1]-aggregate[2]-aggregate[3]-aggregate[4]})}


@router.get('/workspace/options')
def options(kind: str, db: Database, actor=Depends(get_actor), query: str = Query('',max_length=200), offset: int = Query(0,ge=0),after: str | None = Query(None,max_length=36)):
    kinds={
        'warehouses':(Warehouse,'warehouses.read',['name']),
        'parties':(Party,'parties.read',['name','phone']),
        'receipts':(Receipt,'cargo.read',['reference','customer_phone']),
        'tickets':(Ticket,'packing.read',['receipt','mark','contact_raw']),
        'shipments':(Shipment,'shipments.read',['reference']),
        'documents':(PackingDocument,'packing.read',['reference']),
        'invoices':(Invoice,'finance.read',['reference']),
        'payables':(Invoice,'payables.read',['reference']),
        'payments':(Payment,'finance.read',['source_key','period']),
        'evidence':(EvidenceCase,'evidence.read',['business_reference','payer_name']),
        'accounts':(BankAccount,'bank.read',['bank_name','account_number']),
        'entries':(BankEntry,'bank.read',['reference','description']),
        'claims':(BankClaim,'bank.read',['state']),
        'legacy-bills':(LegacyBill,'legacy.global_read',['reference']),
        'legacy-tickets':(LegacyTicket,'legacy.global_read',['receipt','consignee']),
        'legacy-parties':(LegacyParty,'legacy.global_read',['name']),
        'legacy-shipments':(LegacyShipment,'legacy.global_read',['source_no','source_bl'])}
    if kind not in kinds: raise DomainError('invalid_kind','Unknown selection',422)
    model,capability,names=kinds[kind]; require(actor,capability)
    if kind.startswith('legacy-'): require(actor,'finance.read')
    statement=select(model)
    if model is Invoice: statement=statement.where(Invoice.kind==('AP' if kind=='payables' else 'AR'),Invoice.scope=='live')
    if model is Payment: statement=statement.where(Payment.direction=='in',Payment.scope=='live')
    if model is BankClaim:statement=statement.join(BankEntry,BankEntry.id==BankClaim.entry_id).join(EvidenceCase,EvidenceCase.id==BankClaim.case_id)
    if '*' not in actor.capabilities:
        if model is Warehouse: statement=statement.where(Warehouse.id.in_(actor.warehouse_ids))
        elif model is BankAccount: statement=statement.where(BankAccount.id.in_(select(BankAccountWarehouse.account_id).where(BankAccountWarehouse.active.is_(True),BankAccountWarehouse.warehouse_id.in_(actor.warehouse_ids))))
        elif model is Party: statement=statement.where(Party.id.in_(select(PartyWarehouse.party_id).where(PartyWarehouse.active.is_(True),PartyWarehouse.warehouse_id.in_(actor.warehouse_ids))))
        elif hasattr(model,'warehouse_id'): statement=statement.where(model.warehouse_id.in_(actor.warehouse_ids))
        elif model in (BankEntry,BankClaim):
            accounts=select(BankAccountWarehouse.account_id).where(BankAccountWarehouse.active.is_(True),BankAccountWarehouse.warehouse_id.in_(actor.warehouse_ids))
            if model is BankEntry: statement=statement.where(BankEntry.account_id.in_(accounts))
            else: statement=statement.where(BankEntry.account_id.in_(accounts),EvidenceCase.warehouse_id.in_(actor.warehouse_ids))
    columns=[model.id]+[getattr(model,n) for n in names if hasattr(model,n)]
    if model is BankClaim:columns += [BankClaim.entry_id,BankClaim.case_id,BankEntry.reference,EvidenceCase.business_reference]
    if query: statement=statement.where(or_(*(c.ilike('%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%',escape='\\') for c in columns)))
    # Only authorized IDs may become public cursors. A same-warehouse claim can
    # still fail message-context authorization, or a payment can span warehouses.
    # Scan bounded chunks until 51 visible rows; never return a rejected row ID.
    result=[];cursor=after;skip=offset if not after else 0
    while len(result)<51:
        scoped=statement.where(model.id>cursor) if cursor else statement
        page=list(db.scalars(scoped.order_by(model.id).offset(skip).limit(51)));skip=0
        if not page:break
        entries={e.id:e for e in db.scalars(select(BankEntry).where(BankEntry.id.in_([r.entry_id for r in page])))} if model is BankClaim else {}
        for row in page:
            try:
                if model is Payment:
                    from app.finance.access import payment_access
                    payment_access(db,actor,row,'finance.read')
                elif model is BankAccount: account_access(db,actor,row,'bank.read')
                elif model is BankEntry: account_access(db,actor,db.get(BankAccount,row.account_id),'bank.read')
                elif model is BankClaim: claim_detail(row.id,db,actor)
            except DomainError: continue
            text=' · '.join(str(getattr(row,n)) for n in names if getattr(row,n,None)) or row.id
            if model is Payment:text=' · '.join(str(v) for v in [row.id,row.paid_on,row.currency,row.amount,row.source_key] if v is not None)
            if model is BankClaim:
                entry=entries[row.entry_id]
                text=' · '.join(str(v) for v in [row.id,entry.reference,entry.transaction_date,entry.currency,entry.credit,row.state] if v is not None)
            result.append({'id':row.id,'version':row.version,'label':text})
            if len(result)==51:break
        if len(page)<51:break
        cursor=page[-1].id
    return {'items':result[:50],'next':result[49]['id'] if len(result)>50 else None}


@router.get('/workspace/bank-entry/{identity}')
def entry_detail(identity: str,db: Database,actor=Depends(get_actor)):
    from app.models import BankSourceRow,BankFile
    row=db.get(BankEntry,identity)
    if not row: raise DomainError('not_found','Bank entry unavailable',404)
    account=db.get(BankAccount,row.account_id);account_access(db,actor,account,'bank.read')
    sources=[record(source)|{'attachment_id':file.attachment_id,'file_sha256':file.sha256} for source,file in db.execute(select(BankSourceRow,BankFile).join(BankFile,BankFile.id==BankSourceRow.file_id).where(BankSourceRow.entry_id==row.id).order_by(BankSourceRow.id))]
    return {**record(row),'account_version':account.version,'sources':sources}


@router.get('/workspace/bank-claim/{identity}')
def claim_detail(identity: str,db: Database,actor=Depends(get_actor)):
    row=db.get(BankClaim,identity)
    if not row: raise DomainError('not_found','Bank claim unavailable',404)
    entry=db.get(BankEntry,row.entry_id);account=db.get(BankAccount,entry.account_id)
    account_access(db,actor,account,'bank.read')
    from app.finance.authorization import case_read
    case=db.get(EvidenceCase,row.case_id);case_read(db,actor,case)
    if row.payment_id:
        from app.finance.access import payment_access
        payment_access(db,actor,db.get(Payment,row.payment_id),'finance.read')
    return {**record(row),'versions':{'claim_version':row.version,'case_version':case.version,'entry_version':entry.version,'account_version':account.version}}


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NoticeCreate(Input):
    title_zh: str = Field(min_length=1, max_length=200)
    title_en: str = Field(min_length=1, max_length=200)
    body_zh: str = Field(max_length=10000)
    body_en: str = Field(max_length=10000)
    active: bool = True


class NoticePatch(Input):
    expected_version: int = Field(ge=1)
    title_zh: str | None = Field(default=None, min_length=1, max_length=200)
    title_en: str | None = Field(default=None, min_length=1, max_length=200)
    body_zh: str | None = Field(default=None, max_length=10000)
    body_en: str | None = Field(default=None, max_length=10000)
    active: bool | None = None


@router.get('/announcements')
def announcements(db: Database, actor=Depends(get_actor)):
    query = select(Announcement).order_by(Announcement.created_at.desc()).limit(100)
    if '*' not in actor.capabilities and 'announcements.manage' not in actor.capabilities:
        query = query.where(Announcement.active.is_(True))
    return {'items': [record(row) for row in db.scalars(query)]}


@router.post('/announcements')
def create_notice(payload: NoticeCreate, request: Request, db: Database, actor=Depends(mutate)):
    require(actor, 'announcements.manage')
    def apply():
        row = Announcement(**payload.model_dump(), actor_id=actor.id)
        db.add(row); db.flush()
        result = record(row)
        audit(db, actor, 'announcement.create', 'announcement', row.id, None, result)
        return result
    return run_command(db, actor, 'announcement.create', request.headers.get('Idempotency-Key'), payload.model_dump(), apply)


@router.patch('/announcements/{identity}')
def update_notice(identity: str, payload: NoticePatch, request: Request, db: Database, actor=Depends(mutate)):
    require(actor, 'announcements.manage')
    def apply():
        row = db.scalar(select(Announcement).where(Announcement.id == identity).with_for_update())
        if not row: raise DomainError('not_found', 'Announcement unavailable', 404)
        check_version(row, payload.expected_version)
        before = record(row)
        for name, value in payload.model_dump(exclude_unset=True).items():
            if name != 'expected_version' and value is not None: setattr(row, name, value)
        row.version += 1; row.actor_id = actor.id
        audit(db, actor, 'announcement.update', 'announcement', row.id, before, record(row))
        return record(row)
    return run_command(db, actor, 'announcement.update:' + identity, request.headers.get('Idempotency-Key'), payload.model_dump(exclude_unset=True), apply)


@router.get('/admin/users/{identity}/grants')
def user_grants(identity: str, db: Database, actor=Depends(get_actor)):
    require(actor, 'users.read')
    from app.models import User
    user=db.get(User,identity)
    if not user:raise DomainError('not_found','User unavailable',404)
    return {'id':identity,'version':user.version,'capabilities': list(db.scalars(select(Grant.capability).where(Grant.user_id == identity).order_by(Grant.capability))),
        'warehouse_ids': list(db.scalars(select(WarehouseScope.warehouse_id).where(WarehouseScope.user_id == identity)))}


@router.get('/workspace/search')
def search(db: Database, actor=Depends(get_actor), query: str = Query('', max_length=200), limit: int = Query(50, ge=1, le=100)):
    if not query.strip(): return {'items': []}
    pattern = '%' + query.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
    results = []
    kinds = [(Party,'parties','parties.read',[Party.name,Party.phone]),
        (Receipt,'receipts','cargo.read',[Receipt.reference,Receipt.customer_phone,Receipt.item_zh]),
        (Ticket,'tickets','packing.read',[Ticket.receipt,Ticket.mark,Ticket.contact_raw]),
        (Shipment,'shipments','shipments.read',[Shipment.reference]),
        (Invoice,'invoices','finance.read',[Invoice.reference]),
        (Invoice,'payables','payables.read',[Invoice.reference])]
    for model, kind, capability, columns in kinds:
        if '*' not in actor.capabilities and capability not in actor.capabilities: continue
        statement = select(model)
        conditions=[c.ilike(pattern, escape='\\') for c in columns]
        if model in (Ticket,Invoice,Receipt):
            statement=statement.outerjoin(Party,Party.id==model.party_id)
            conditions.extend([Party.name.ilike(pattern,escape='\\'),Party.phone.ilike(pattern,escape='\\')])
        if model is Ticket:
            statement=statement.join(PackingDocument,PackingDocument.id==Ticket.document_id).join(Shipment,Shipment.id==PackingDocument.shipment_id)
            conditions.append(Shipment.reference.ilike(pattern,escape='\\'))
        statement=statement.where(or_(*conditions))
        if model is Invoice: statement = statement.where(Invoice.kind == ('AP' if kind=='payables' else 'AR'), Invoice.scope == 'live')
        if '*' not in actor.capabilities:
            if model is Party:statement=statement.where(Party.id.in_(select(PartyWarehouse.party_id).where(PartyWarehouse.active.is_(True),PartyWarehouse.warehouse_id.in_(actor.warehouse_ids))))
            else:statement = statement.where(model.warehouse_id.in_(actor.warehouse_ids))
        for row in db.scalars(statement.order_by(model.id).limit(max(0,limit-len(results)))):
            results.append({'id':row.id,'kind':kind,'reference':getattr(row,'reference',None) or getattr(row,'receipt',None) or getattr(row,'name',None), 'version':row.version})
        if len(results) >= limit: break
    if len(results)<limit and ('*' in actor.capabilities or 'legacy.global_read' in actor.capabilities):
        for model,kind,names in [(LegacyParty,'legacy-parties',['name','phone']),(LegacyShipment,'legacy-shipments',['source_no','source_bl']),(LegacyTicket,'legacy-tickets',['receipt','consignee','contact']),(LegacyBill,'legacy-bills',['reference','source_party_ref','source_shipment_ref'])]:
            if model in (LegacyTicket,LegacyBill) and '*' not in actor.capabilities and 'finance.read' not in actor.capabilities:continue
            columns=[getattr(model,n) for n in names if hasattr(model,n)]
            statement=select(model).where(or_(*(c.ilike(pattern,escape='\\') for c in columns))).order_by(model.id).limit(limit-len(results))
            for row in db.scalars(statement):results.append({'id':row.id,'kind':kind,'reference':getattr(row,'reference',None) or getattr(row,'receipt',None) or getattr(row,'name',None) or getattr(row,'source_no',None),'version':row.version})
            if len(results)>=limit:break
    return {'items':results}


@router.get('/workspace/bank-entries')
def bank_entries(account_id: str, db: Database, actor=Depends(get_actor), query: str = Query('', max_length=200), after: str | None = None, limit: int = Query(50, ge=1, le=200)):
    account = db.get(BankAccount, account_id)
    if not account: raise DomainError('not_found','Bank account unavailable',404)
    account_access(db,actor,account,'bank.read')
    statement = select(BankEntry).where(BankEntry.account_id==account_id)
    if after: statement=statement.where(BankEntry.id>after)
    if query: statement=statement.where(or_(BankEntry.reference.ilike('%'+query+'%'),BankEntry.description.ilike('%'+query+'%')))
    rows=list(db.scalars(statement.order_by(BankEntry.id).limit(limit+1)))
    return {'items':[record(r) for r in rows[:limit]],'next': rows[limit-1].id if len(rows)>limit else None}


@router.get('/workspace/mbl')
def mbl_list(db: Database, actor=Depends(get_actor), offset: int = Query(0,ge=0), limit: int = Query(50,ge=1,le=200)):
    require(actor,'documents.read')
    statement=select(MBLRecord).join(Shipment,Shipment.id==MBLRecord.shipment_id)
    if '*' not in actor.capabilities: statement=statement.where(Shipment.warehouse_id.in_(actor.warehouse_ids))
    return {'items':[record(r) for r in db.scalars(statement.order_by(MBLRecord.id).offset(offset).limit(limit))]}


class PublicWarehouse(Input):
    name: str = Field(min_length=1,max_length=200)
    address: str = Field(max_length=2000)
    receiving_hours: str = Field(default='',max_length=500)
    contacts: str = Field(default='',max_length=2000)
    country: str = Field(pattern='^(CN|NG)$')


class HomeProfile(Input):
    expected_version: int = Field(ge=0)
    warehouses: list[PublicWarehouse] = Field(default_factory=list,max_length=30)
    hotline: str = Field(default='',max_length=2000)
    openshipment: str = Field(default='',max_length=2000)
    hero_large: str | None = None
    hero_small: str | None = None
    source: str = Field(min_length=1,max_length=2000)


@router.get('/workspace/home-profile')
def home_profile(db: Database,actor=Depends(get_actor)):
    require(actor,'documents.configure')
    row=db.scalar(select(AppSetting).where(AppSetting.key=='public.home'))
    return {'version':row.version if row else 0, **(row.value if row else {})}


@router.post('/workspace/home-profile')
def set_home_profile(payload: HomeProfile,request: Request,db: Database,actor=Depends(mutate)):
    require(actor,'documents.configure')
    def apply():
        for identity in (payload.hero_large,payload.hero_small):
            if not identity: continue
            attachment=db.get(Attachment,identity)
            if not attachment or attachment.created_by!=actor.id or attachment.media_type not in ('image/png','image/jpeg','image/webp'):
                raise DomainError('image_unavailable','Select an owned original image',422)
            from PIL import Image
            from io import BytesIO
            with Image.open(BytesIO(read_file(db.info['settings'],attachment))) as im:
                im.verify()
        from sqlalchemy import text
        db.execute(text('SELECT pg_advisory_xact_lock(110006)'))
        row=db.scalar(select(AppSetting).where(AppSetting.key=='public.home').with_for_update())
        if row: check_version(row,payload.expected_version)
        elif payload.expected_version: raise DomainError('stale_version','Profile changed',409)
        before=record(row) if row else None
        if not row:
            row=AppSetting(key='public.home',value={});db.add(row);db.flush()
        else: row.version+=1
        row.value=payload.model_dump(exclude={'expected_version'})
        audit(db,actor,'public_profile.update','setting',row.id,before,record(row))
        return {'version':row.version,**row.value}
    return run_command(db,actor,'public_profile.update',request.headers.get('Idempotency-Key'),payload.model_dump(),apply)


@router.get('/public/assets/{position}')
def public_asset(position: str,db: Database):
    if position not in ('hero_large','hero_small'): raise DomainError('not_found','Image unavailable',404)
    row=db.scalar(select(AppSetting).where(AppSetting.key=='public.home'))
    attachment=db.get(Attachment,row.value.get(position)) if row and row.value.get(position) else None
    if not attachment: raise DomainError('not_found','Image unavailable',404)
    return Response(read_file(db.info['settings'],attachment),media_type=attachment.media_type,headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})
