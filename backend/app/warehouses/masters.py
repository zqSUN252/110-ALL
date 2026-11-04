import re
from sqlalchemy import select,case,exists,or_,and_
from app.common import DomainError,require,check_version
from app.models import Party,Warehouse,PartyCategory,PartyPhone,PartyWarehouse
from app.finance.service import record
from app.cargo.commands import command,object_access


def normalized_phone(raw):
    value=re.sub(r'[\s()-]','',str(raw or ''))
    # National numbers stay raw unless an explicit international country code exists.
    return value if re.fullmatch(r'\+[1-9][0-9]{7,14}',value) else None


def party_json(db,p):
    phones=[record(x) for x in db.scalars(select(PartyPhone).where(PartyPhone.party_id==p.id).order_by(PartyPhone.created_at,PartyPhone.id))]
    active=next((x['normalized'] for x in reversed(phones) if x['active'] and x['normalized']),None)
    return record(p)|{'categories':[r.category for r in db.scalars(select(PartyCategory).where(PartyCategory.party_id==p.id,PartyCategory.active.is_(True)).order_by(PartyCategory.category))],
        'phones':phones,'whatsapp_url':'https://wa.me/'+active[1:] if active else None,
        'memberships':[record(m) for m in db.scalars(select(PartyWarehouse).where(PartyWarehouse.party_id==p.id))]}


def get_party(db,actor,identity):
    return party_json(db,object_access(db,actor,Party,identity,'parties.read'))


@command('party.update','id expected_version name classification address source phone phone_source categories','parties.manage',Party)
def update_party(db,actor,p):
    obj=object_access(db,actor,Party,p['id'],'parties.manage');check_version(obj,p.get('expected_version'))
    # Global identity edits affect every membership, not just its home warehouse.
    for m in db.scalars(select(PartyWarehouse).where(PartyWarehouse.party_id==obj.id,PartyWarehouse.active.is_(True))):require(actor,'parties.manage',m.warehouse_id)
    for k in ('name','classification','address','source'):
        if k in p:
            if k=='name' and not str(p[k] or '').strip():raise DomainError('name_required','Party name required')
            setattr(obj,k,p[k])
    if 'phone' in p:
        if not str(p.get('phone_source','')).strip():raise DomainError('phone_source','Phone changes require original source or manual provenance')
        previous=list(db.scalars(select(PartyPhone).where(PartyPhone.party_id==obj.id,PartyPhone.active.is_(True))))
        if not previous and obj.phone:
            db.add(PartyPhone(party_id=obj.id,raw=obj.phone,normalized=normalized_phone(obj.phone),source=obj.source or 'Pre-existing phone; original source unknown',actor_id=actor.id,active=False))
        for x in previous:x.active=False;x.version+=1
        db.add(PartyPhone(party_id=obj.id,raw=p['phone'] or '',normalized=normalized_phone(p['phone']),source=p['phone_source'],actor_id=actor.id))
        obj.phone=p['phone']
    if 'categories' in p:
        if not isinstance(p['categories'],list) or len(p['categories'])>30 or any(not isinstance(c,str) or not c.strip() or len(c)>100 for c in p['categories']):raise DomainError('categories','Provide explicit categories')
        existing={c.category:c for c in db.scalars(select(PartyCategory).where(PartyCategory.party_id==obj.id))}
        for category,row in existing.items():row.active=category in p['categories'];row.version+=1
        for category in set(p['categories'])-existing.keys():db.add(PartyCategory(party_id=obj.id,category=category))
    obj.version+=1;db.flush();return party_json(db,obj)


@command('warehouse.update','id expected_version name address contact source','warehouses.manage',Warehouse)
def update_warehouse(db,actor,p):
    obj=object_access(db,actor,Warehouse,p['id'],'warehouses.manage');check_version(obj,p.get('expected_version'))
    if not str(p.get('source','')).strip():raise DomainError('warehouse_source','Explicit address/contact source required')
    for k in ('name','address','contact','source'):
        if k in p:setattr(obj,k,p[k])
    obj.version+=1;db.flush();return record(obj)|{'warehouse_id':obj.id}


def get_warehouse(db,actor,identity):return record(object_access(db,actor,Warehouse,identity,'warehouses.read'))


def list_parties(db,actor,warehouse_id,after=None,limit=50,query=None,category=None):
    require(actor,'parties.read',warehouse_id)
    if not 1<=limit<=200:raise DomainError('pagination','Limit must be 1..200')
    warehouse_category=or_(Party.classification.ilike('%warehouse%'),Party.classification.like('%仓%'),exists(select(PartyCategory.id).where(PartyCategory.party_id==Party.id,PartyCategory.active.is_(True),or_(PartyCategory.category.ilike('%warehouse%'),PartyCategory.category.like('%仓%')))))
    priority=case((warehouse_category,0),else_=1)
    q=select(Party,priority.label('priority')).join(PartyWarehouse,PartyWarehouse.party_id==Party.id).where(PartyWarehouse.warehouse_id==warehouse_id,PartyWarehouse.active.is_(True))
    if query:q=q.where(Party.name.icontains(query,autoescape=True))
    if category:q=q.where(exists(select(PartyCategory.id).where(PartyCategory.party_id==Party.id,PartyCategory.active.is_(True),PartyCategory.category==category)))
    if after:
        try:p,identity=after.split(':',1);p=int(p)
        except ValueError:raise DomainError('cursor','Invalid party cursor') from None
        q=q.where(or_(priority>p,and_(priority==p,Party.id>identity)))
    rows=list(db.execute(q.order_by(priority,Party.id).limit(limit)))
    return {'items':[party_json(db,p) for p,_ in rows],'next':f'{rows[-1][1]}:{rows[-1][0].id}' if len(rows)==limit else None}
