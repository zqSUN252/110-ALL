"""Capture owner-phone facts on every ORM import/edit path, before strings can be erased."""
import re
from uuid import uuid4
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session
from app.models import Ticket, Receipt, HoldFact, CargoPhoneHistory


def marker(value):
    return bool(re.search(r'\bON\s+HOLD\b', value or '', re.I))


@event.listens_for(Session, 'before_flush')
def retain_owner_phone(db, context, instances):
    for obj in list(db.new) + list(db.dirty):
        if not isinstance(obj, (Ticket, Receipt)):
            continue
        field = 'contact_raw' if isinstance(obj, Ticket) else 'customer_phone'
        state = inspect(obj)
        history = state.attrs[field].history
        normalized = obj.phone_normalized if isinstance(obj, Ticket) and obj.phone_confirmed else None
        phone_changed = obj in db.new or history.has_changes() or (isinstance(obj,Ticket) and (state.attrs.phone_normalized.history.has_changes() or state.attrs.phone_confirmed.history.has_changes()))
        if not phone_changed:
            continue
        obj.id = obj.id or str(uuid4())
        key = 'ticket_id' if isinstance(obj,Ticket) else 'receipt_id'
        raw = getattr(obj,field)
        if obj in db.new and raw is None and normalized is None:
            continue  # The source row already preserves initial unknown; no phone observation exists yet.
        pending=db.info.setdefault('_phone_facts',[])
        pending.append((CargoPhoneHistory,dict(**{key:obj.id}, original=raw, confirmed=normalized, source_version=obj.version or 1)))
        old = history.deleted[0] if history.deleted else None
        if (marker(raw) or marker(old)) and obj.id not in db.info.get('hold_removing',set()):
            exists = any(cls is HoldFact and data.get(key)==obj.id for cls,data in pending)
            exists = exists or db.scalar(select(HoldFact.id).where(getattr(HoldFact,key)==obj.id,HoldFact.state=='active'))
            if not exists:
                pending.append((HoldFact,dict(**{key:obj.id},original_phone=raw if marker(raw) else old,source_version=obj.version or 1,state='active')))


@event.listens_for(Session,'after_flush_postexec')
def write_phone_facts(db,context):
    # Sources now exist; Core INSERT keeps all facts in the caller's transaction.
    for cls,values in db.info.pop('_phone_facts',[]):
        db.execute(cls.__table__.insert().values(**values))


@event.listens_for(Session,'after_rollback')
def discard_uncommitted_phone_facts(db):
    db.info.pop('_phone_facts',None)
