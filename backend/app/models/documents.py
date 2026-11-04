"""Typed document, instruction and customer facts; JSON only preserves immutable snapshots."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import String, Text, Integer, Boolean, Date, DateTime, Numeric, ForeignKey, UniqueConstraint, CheckConstraint,Index,text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column as col
from app.db import Base
from app.models import Record


class HoldFact(Record, Base):
    __tablename__='manufacturer_holds'
    __table_args__=(CheckConstraint('(ticket_id IS NULL) <> (receipt_id IS NULL)'),)
    ticket_id:Mapped[str|None]=col(ForeignKey('tickets.id'),index=True)
    receipt_id:Mapped[str|None]=col(ForeignKey('receipts.id'),index=True)
    original_phone:Mapped[str]=col(Text)
    source_version:Mapped[int]=col(Integer)
    state:Mapped[str]=col(String(16),default='active')
    removed_on:Mapped[date|None]=col(Date)
    reason:Mapped[str|None]=col(Text)
    confirmed_phone:Mapped[str|None]=col(String(30))
    removal_ngn_per_usd:Mapped[Decimal|None]=col(Numeric(20,6))
    removed_by:Mapped[str|None]=col(ForeignKey('users.id'))
    cancelled_by:Mapped[str|None]=col(ForeignKey('users.id'))
    cancellation_reason:Mapped[str|None]=col(Text)


class CargoPhoneHistory(Record, Base):
    __tablename__='cargo_phone_history'
    __table_args__=(CheckConstraint('(ticket_id IS NULL) <> (receipt_id IS NULL)'),)
    ticket_id:Mapped[str|None]=col(ForeignKey('tickets.id'),index=True)
    receipt_id:Mapped[str|None]=col(ForeignKey('receipts.id'),index=True)
    original:Mapped[str|None]=col(Text)
    confirmed:Mapped[str|None]=col(String(30))
    source_version:Mapped[int]=col(Integer)


class ReleaseOrder(Record, Base):
    __tablename__='release_orders'
    mode:Mapped[str]=col(String(16))
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    promise_on:Mapped[date|None]=col(Date)
    guarantor:Mapped[str|None]=col(Text)
    reason:Mapped[str|None]=col(Text)
    status:Mapped[str]=col(String(16),default='issued')


class ReleaseMember(Record, Base):
    __tablename__='release_members'
    __table_args__=(UniqueConstraint('order_id','ticket_id'),)
    order_id:Mapped[str]=col(ForeignKey('release_orders.id'),index=True)
    ticket_id:Mapped[str]=col(ForeignKey('tickets.id'),index=True)
    destination_warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    source_fingerprint:Mapped[str]=col(String(64))
    source_snapshot:Mapped[dict]=col(JSONB)


class PhysicalRelease(Record, Base):
    __tablename__='physical_releases'
    __table_args__=(CheckConstraint('pieces > 0 AND kg >= 0 AND cbm >= 0'),)
    order_id:Mapped[str]=col(ForeignKey('release_orders.id'))
    ticket_id:Mapped[str]=col(ForeignKey('tickets.id'),index=True)
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    occurred_at:Mapped[datetime]=col(DateTime(timezone=True))
    pieces:Mapped[int]=col(Integer)
    kg:Mapped[Decimal]=col(Numeric(20,6))
    cbm:Mapped[Decimal]=col(Numeric(20,6))


class PhysicalPhoto(Record, Base):
    __tablename__='physical_photos'
    __table_args__=(UniqueConstraint('release_id','attachment_id'),)
    release_id:Mapped[str]=col(ForeignKey('physical_releases.id'))
    attachment_id:Mapped[str]=col(ForeignKey('attachments.id'))


class PaymentRegistration(Record, Base):
    __tablename__='payment_registrations'
    __table_args__=(CheckConstraint('(payment_id IS NULL) <> (case_id IS NULL)'),)
    invoice_id:Mapped[str]=col(ForeignKey('invoices.id'),index=True)
    payment_id:Mapped[str|None]=col(ForeignKey('payments.id'))
    case_id:Mapped[str|None]=col(ForeignKey('evidence_cases.id'))
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    source:Mapped[str]=col(Text)
    active:Mapped[bool]=col(Boolean,default=True)


class Notification(Record, Base):
    __tablename__='notifications'
    kind:Mapped[str]=col(String(24))
    template_version:Mapped[str]=col(String(40))
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    generated_on:Mapped[date]=col(Date)
    timezone:Mapped[str]=col(String(80))
    body:Mapped[str]=col(Text)
    source_snapshot:Mapped[dict]=col(JSONB)
    order_id:Mapped[str|None]=col(ForeignKey('release_orders.id'))


class NotificationMember(Record, Base):
    __tablename__='notification_members'
    __table_args__=(CheckConstraint('(ticket_id IS NULL) <> (receipt_id IS NULL)'),)
    notification_id:Mapped[str]=col(ForeignKey('notifications.id'),index=True)
    ticket_id:Mapped[str|None]=col(ForeignKey('tickets.id'),index=True)
    receipt_id:Mapped[str|None]=col(ForeignKey('receipts.id'))
    source_version:Mapped[int]=col(Integer)
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))


class CopyEvent(Record, Base):
    __tablename__='notification_copy_events'
    notification_id:Mapped[str]=col(ForeignKey('notifications.id'),unique=True)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    cancelled_by:Mapped[str|None]=col(ForeignKey('users.id'))
    cancelled_at:Mapped[datetime|None]=col(DateTime(timezone=True))
    reason:Mapped[str|None]=col(Text)


class TrackingNode(Record, Base):
    __tablename__='tracking_nodes'
    __table_args__=(CheckConstraint('(ticket_id IS NULL) <> (shipment_id IS NULL)'),UniqueConstraint('ticket_id','node'),Index('uq_tracking_shipment_node_nonwarehouse','shipment_id','node',unique=True,postgresql_where=text("node <> 'warehouse_arrived'")),Index('uq_tracking_shipment_warehouse','shipment_id','node','destination_warehouse_id',unique=True,postgresql_where=text("node = 'warehouse_arrived'")))
    ticket_id:Mapped[str|None]=col(ForeignKey('tickets.id'))
    shipment_id:Mapped[str|None]=col(ForeignKey('shipments.id'))
    node:Mapped[str]=col(String(40))
    mode:Mapped[str]=col(String(12))
    occurred_at:Mapped[datetime|None]=col(DateTime(timezone=True))
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    source:Mapped[str]=col(Text)
    destination_warehouse_id:Mapped[str|None]=col(ForeignKey('warehouses.id',name='fk_tracking_destination_warehouse'))
    coverage:Mapped[dict|None]=col(JSONB)


class DocumentExport(Record, Base):
    __tablename__='document_exports'
    job_id:Mapped[str]=col(ForeignKey('jobs.id'),unique=True)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    customer_id:Mapped[str|None]=col(ForeignKey('customer_identities.id'))
    format:Mapped[str]=col(String(8))
    kind:Mapped[str]=col(String(24))
    source_fingerprint:Mapped[str|None]=col(String(64))
    source_snapshot:Mapped[dict|None]=col(JSONB)
    attachment_id:Mapped[str|None]=col(ForeignKey('attachments.id'))
    mbl_id:Mapped[str|None]=col(ForeignKey('mbl_records.id'))


class ExportMember(Record, Base):
    __tablename__='document_export_members'
    __table_args__=(CheckConstraint('(ticket_id IS NULL) <> (receipt_id IS NULL)'),)
    export_id:Mapped[str]=col(ForeignKey('document_exports.id'),index=True)
    ticket_id:Mapped[str|None]=col(ForeignKey('tickets.id'))
    receipt_id:Mapped[str|None]=col(ForeignKey('receipts.id'))


class MBLRecord(Record, Base):
    __tablename__='mbl_records'
    shipment_id:Mapped[str]=col(ForeignKey('shipments.id'),unique=True)
    number:Mapped[str|None]=col(String(200))
    shipper:Mapped[str|None]=col(Text)
    consignee:Mapped[str|None]=col(Text)
    notify_party:Mapped[str|None]=col(Text)
    vessel:Mapped[str|None]=col(String(200))
    voyage:Mapped[str|None]=col(String(100))
    port_loading:Mapped[str|None]=col(String(200))
    port_discharge:Mapped[str|None]=col(String(200))
    final_destination:Mapped[str|None]=col(String(200))
    description:Mapped[str|None]=col(Text)
    marks:Mapped[str|None]=col(Text)
    packages:Mapped[int|None]=col(Integer)
    gross_kg:Mapped[Decimal|None]=col(Numeric(20,6))
    cbm:Mapped[Decimal|None]=col(Numeric(20,6))
    freight_terms:Mapped[str|None]=col(String(200))
    booking_no:Mapped[str|None]=col(String(200))
    sailing_on:Mapped[date|None]=col(Date)


class MBLAttachment(Record, Base):
    __tablename__='mbl_attachments'
    mbl_id:Mapped[str]=col(ForeignKey('mbl_records.id'),index=True)
    attachment_id:Mapped[str]=col(ForeignKey('attachments.id'))
    kind:Mapped[str]=col(String(12))
    source_version:Mapped[int]=col(Integer)


class DocumentTemplate(Record, Base):
    __tablename__='document_templates'
    kind:Mapped[str]=col(String(12),unique=True)
    attachment_id:Mapped[str]=col(ForeignKey('attachments.id'))
    field_mapping:Mapped[dict]=col(JSONB)  # immutable template cell mapping, not business records
    active:Mapped[bool]=col(Boolean,default=True)


class CustomerIdentity(Record, Base):
    __tablename__='customer_identities'
    phone:Mapped[str]=col(String(30),unique=True)
    user_id:Mapped[str]=col(ForeignKey('users.id'),unique=True)
    active:Mapped[bool]=col(Boolean,default=True)
    verified_at:Mapped[datetime]=col(DateTime(timezone=True))


class CustomerSession(Record, Base):
    __tablename__='customer_sessions'
    customer_id:Mapped[str]=col(ForeignKey('customer_identities.id'))
    token_hash:Mapped[str]=col(String(64),unique=True)
    csrf_hash:Mapped[str]=col(String(64))
    expires_at:Mapped[datetime]=col(DateTime(timezone=True))
    revoked_at:Mapped[datetime|None]=col(DateTime(timezone=True))


class PhoneChallenge(Record, Base):
    __tablename__='phone_challenges'
    phone:Mapped[str]=col(String(30))
    code_hash:Mapped[str]=col(Text)
    expires_at:Mapped[datetime]=col(DateTime(timezone=True))
    attempts:Mapped[int]=col(Integer,default=0)
    consumed:Mapped[bool]=col(Boolean,default=False)
    status:Mapped[str]=col(String(16),default='pending')
    provider_reference:Mapped[str|None]=col(String(200))


class PublicAttempt(Record, Base):
    __tablename__='public_attempts'
    bucket:Mapped[str]=col(String(64),unique=True)
    window_start:Mapped[datetime]=col(DateTime(timezone=True))
    count:Mapped[int]=col(Integer,default=0)
