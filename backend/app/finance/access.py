"""Identity participation is explicit; financial authorization covers every affected warehouse."""
from sqlalchemy import select
from app.common import DomainError,require
from app.models import PartyWarehouse,BankAccountWarehouse,Invoice,Allocation,EvidenceCase,Payment


def party_member(db,party_id,warehouse_id):
    if not db.scalar(select(PartyWarehouse.id).where(PartyWarehouse.party_id==party_id,PartyWarehouse.warehouse_id==warehouse_id,PartyWarehouse.active.is_(True))):
        raise DomainError('party_scope','Customer has no active participation in this warehouse',403)


def account_access(db,actor,account,capability,warehouse_id=None):
    memberships=set(db.scalars(select(BankAccountWarehouse.warehouse_id).where(BankAccountWarehouse.account_id==account.id,BankAccountWarehouse.active.is_(True))))
    if warehouse_id is not None:
        if warehouse_id not in memberships:raise DomainError('account_scope','Account not assigned to this warehouse',403)
        require(actor,capability,warehouse_id)
    else:
        require(actor,capability)
        if '*' not in actor.capabilities and not memberships.intersection(actor.warehouse_ids):
            raise DomainError('account_scope','No authorized warehouse participation for this bank account',403)


def payment_warehouses(db,payment):
    return {payment.warehouse_id}|set(db.scalars(select(Invoice.warehouse_id).join(Allocation,Allocation.invoice_id==Invoice.id).where(Allocation.payment_id==payment.id)))|set(db.scalars(select(EvidenceCase.warehouse_id).where(EvidenceCase.payment_id==payment.id)))


def payment_access(db,actor,payment,capability):
    for warehouse in payment_warehouses(db,payment):require(actor,capability,warehouse)


def visible_payments(query,actor):
    if '*' in actor.capabilities:return query
    inaccessible_invoices=select(Allocation.payment_id).join(Invoice,Invoice.id==Allocation.invoice_id).where(Invoice.warehouse_id.not_in(actor.warehouse_ids))
    inaccessible_cases=select(EvidenceCase.payment_id).where(EvidenceCase.payment_id.is_not(None),EvidenceCase.warehouse_id.not_in(actor.warehouse_ids))
    return query.where(Payment.warehouse_id.in_(actor.warehouse_ids),Payment.id.not_in(inaccessible_invoices),Payment.id.not_in(inaccessible_cases))
