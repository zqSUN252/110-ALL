"""Typed logistics facts. JSON is restricted to immutable source/provenance and quote snapshots."""
from datetime import date
from decimal import Decimal
from sqlalchemy import String, Text, Numeric, Date, ForeignKey, Integer, Boolean, UniqueConstraint, CheckConstraint, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column as col
from app.db import Base
from app.models import Record

Quantity=Numeric(20,6)
Money=Numeric(20,2)


class PartyCategory(Record,Base):
    __tablename__='party_categories'
    __table_args__=(UniqueConstraint('party_id','category'),)
    party_id:Mapped[str]=col(ForeignKey('parties.id'))
    category:Mapped[str]=col(String(100))
    active:Mapped[bool]=col(Boolean,default=True)


class PartyPhone(Record,Base):
    __tablename__='party_phones'
    party_id:Mapped[str]=col(ForeignKey('parties.id'),index=True)
    raw:Mapped[str]=col(Text)
    normalized:Mapped[str|None]=col(String(30))
    source:Mapped[str]=col(Text)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    active:Mapped[bool]=col(Boolean,default=True)


class Receipt(Record,Base):
    __tablename__='receipts'
    __table_args__=(UniqueConstraint('series','number'),CheckConstraint('pieces >= 0 AND kg >= 0 AND cbm >= 0'))
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'),index=True)
    party_id:Mapped[str|None]=col(ForeignKey('parties.id'))
    series:Mapped[str]=col(String(80))
    number:Mapped[int]=col(Integer)
    reference:Mapped[str]=col(String(200))
    received_on:Mapped[date]=col(Date,index=True)
    transport:Mapped[str]=col(String(12))
    origin:Mapped[str|None]=col(String(12))
    goods_class:Mapped[str|None]=col(String(100))
    destination:Mapped[str|None]=col(String(200))
    destination_warehouse_id:Mapped[str|None]=col(ForeignKey('warehouses.id'))
    storage_location:Mapped[str|None]=col(String(200))
    item_zh:Mapped[str|None]=col(Text)
    item_en:Mapped[str|None]=col(Text)
    english_manual_version:Mapped[int]=col(Integer,default=0)
    customer_phone:Mapped[str|None]=col(Text)
    supplier_phone:Mapped[str|None]=col(Text)
    salesperson:Mapped[str|None]=col(String(200))
    operator_name:Mapped[str|None]=col(String(200))
    courier_company:Mapped[str|None]=col(String(200))
    courier_number:Mapped[str|None]=col(String(200),index=True)
    declaration_required:Mapped[bool]=col(Boolean,default=True)
    declaration_source:Mapped[str]=col(String(100),default='new_default')
    pieces:Mapped[int]=col(Integer)
    kg:Mapped[Decimal]=col(Quantity)
    cbm:Mapped[Decimal]=col(Quantity)
    spec_revision:Mapped[int]=col(Integer,default=1)
    state:Mapped[str]=col(String(20),default='active')
    review_state:Mapped[str]=col(String(20),default='pending')
    reviewed_version:Mapped[int|None]=col(Integer)
    quote_id:Mapped[str|None]=col(ForeignKey('quote_snapshots.id',use_alter=True,name='fk_receipt_quote'))


class ReceiptSpec(Record,Base):
    __tablename__='receipt_specs'
    __table_args__=(UniqueConstraint('receipt_id','revision','position'),)
    receipt_id:Mapped[str]=col(ForeignKey('receipts.id'),index=True)
    revision:Mapped[int]=col(Integer)
    position:Mapped[int]=col(Integer)
    length:Mapped[Decimal]=col(Quantity)
    width:Mapped[Decimal]=col(Quantity)
    height:Mapped[Decimal]=col(Quantity)
    unit:Mapped[str]=col(String(6))
    pieces:Mapped[int]=col(Integer)
    kg:Mapped[Decimal]=col(Quantity)
    cbm:Mapped[Decimal]=col(Quantity)
    packing:Mapped[str|None]=col(String(100))


class StockEvent(Record,Base):
    __tablename__='stock_events'
    receipt_id:Mapped[str]=col(ForeignKey('receipts.id'),index=True)
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    kind:Mapped[str]=col(String(20))
    occurred_on:Mapped[date]=col(Date,index=True)
    pieces:Mapped[int]=col(Integer)
    kg:Mapped[Decimal]=col(Quantity)
    cbm:Mapped[Decimal]=col(Quantity)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    reason:Mapped[str|None]=col(Text)
    batch_id:Mapped[str|None]=col(ForeignKey('dispatch_batches.id'))
    source_receipt_id:Mapped[str|None]=col(ForeignKey('receipts.id'))


class ReceiptReview(Record,Base):
    __tablename__='receipt_reviews'
    receipt_id:Mapped[str]=col(ForeignKey('receipts.id'))
    target_version:Mapped[int]=col(Integer)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    decision:Mapped[str]=col(String(20))
    comment:Mapped[str|None]=col(Text)


class TranslationCandidate(Record,Base):
    __tablename__='translation_candidates'
    receipt_id:Mapped[str]=col(ForeignKey('receipts.id'))
    source_text:Mapped[str]=col(Text)
    source_version:Mapped[int]=col(Integer)
    manual_version:Mapped[int]=col(Integer)
    requested_by:Mapped[str]=col(ForeignKey('users.id'))
    status:Mapped[str]=col(String(20),default='pending')
    candidate:Mapped[str|None]=col(Text)
    error_code:Mapped[str|None]=col(String(100))


class PriceCard(Record,Base):
    __tablename__='price_cards'
    transport:Mapped[str]=col(String(12))
    origin:Mapped[str]=col(String(12))
    goods_class:Mapped[str]=col(String(100))
    destination:Mapped[str]=col(String(200))
    unit:Mapped[str]=col(String(6))
    currency:Mapped[str]=col(String(6))
    unit_price:Mapped[Decimal]=col(Money)
    clearing_per_cbm_ngn:Mapped[Decimal|None]=col(Money)
    valid_from:Mapped[date]=col(Date)
    valid_to:Mapped[date]=col(Date)
    active:Mapped[bool]=col(Boolean,default=True)
    source:Mapped[str]=col(Text)


class QuoteSnapshot(Record,Base):
    __tablename__='quote_snapshots'
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    card_id:Mapped[str|None]=col(ForeignKey('price_cards.id'))
    manual:Mapped[bool]=col(Boolean)
    unit:Mapped[str]=col(String(6))
    currency:Mapped[str]=col(String(6))
    unit_price:Mapped[Decimal]=col(Money)
    quantity:Mapped[Decimal]=col(Quantity)
    charge_usd:Mapped[Decimal]=col(Money)
    charge_ngn:Mapped[Decimal|None]=col(Money)
    ngn_per_usd:Mapped[Decimal|None]=col(Quantity)
    amount_ngn:Mapped[Decimal|None]=col(Money)
    basis:Mapped[dict]=col(JSONB)


class Shipment(Record,Base):
    __tablename__='shipments'
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'),index=True)
    reference:Mapped[str]=col(String(200))
    transport:Mapped[str]=col(String(12))
    origin:Mapped[str|None]=col(String(12))
    destination:Mapped[str|None]=col(String(200))
    state:Mapped[str]=col(String(20),default='draft')
    clearing_on:Mapped[date|None]=col(Date)
    clearing_party_id:Mapped[str|None]=col(ForeignKey('parties.id'))
    clearing_amount_ngn:Mapped[Decimal|None]=col(Money)
    clearing_invoice_id:Mapped[str|None]=col(ForeignKey('invoices.id'),unique=True)


class DispatchBatch(Record,Base):
    __tablename__='dispatch_batches'
    shipment_id:Mapped[str]=col(ForeignKey('shipments.id'))
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    occurred_on:Mapped[date]=col(Date)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    reference:Mapped[str|None]=col(String(200))


class PackingDocument(Record,Base):
    __tablename__='packing_documents'
    shipment_id:Mapped[str]=col(ForeignKey('shipments.id'),index=True)
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    reference:Mapped[str]=col(String(200))
    uploaded_on:Mapped[date]=col(Date)
    state:Mapped[str]=col(String(20),default='active')
    parent_id:Mapped[str|None]=col(ForeignKey('packing_documents.id'))
    void_members:Mapped[list|None]=col(JSONB)


class ImportBatch(Record,Base):
    __tablename__='cargo_import_batches'
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    attachment_id:Mapped[str]=col(ForeignKey('attachments.id'))
    kind:Mapped[str]=col(String(12))
    mapping:Mapped[dict]=col(JSONB)
    issues:Mapped[list]=col(JSONB)
    status:Mapped[str]=col(String(20),default='preview')
    document_id:Mapped[str|None]=col(ForeignKey('packing_documents.id'))


class Ticket(Record,Base):
    __tablename__='tickets'
    __table_args__=(CheckConstraint('pieces >= 0 AND kg >= 0 AND cbm >= 0'),CheckConstraint("pricing_authority IN ('quote','totals','pending','review')",name='ck_ticket_pricing_authority'),Index('ix_tickets_created_page',text('created_at DESC'),'id'),Index('ix_tickets_warehouse_created_page','warehouse_id',text('created_at DESC'),'id'))
    document_id:Mapped[str]=col(ForeignKey('packing_documents.id'),index=True)
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    party_id:Mapped[str|None]=col(ForeignKey('parties.id'))
    native_receipt_id:Mapped[str|None]=col(ForeignKey('receipts.id'))
    position:Mapped[int]=col(Integer)
    receipt:Mapped[str|None]=col(String(200))
    mark:Mapped[str|None]=col(String(300))
    item_zh:Mapped[str|None]=col(Text)
    item_en:Mapped[str|None]=col(Text)
    contact_raw:Mapped[str|None]=col(Text)
    phone_normalized:Mapped[str|None]=col(String(30))
    phone_confirmed:Mapped[bool]=col(Boolean,default=False)
    address:Mapped[str|None]=col(Text)
    destination:Mapped[str|None]=col(String(200))
    destination_warehouse_id:Mapped[str|None]=col(ForeignKey('warehouses.id'))
    transport:Mapped[str]=col(String(12))
    origin:Mapped[str|None]=col(String(12))
    goods_class:Mapped[str|None]=col(String(100))
    received_on:Mapped[date|None]=col(Date)
    pieces:Mapped[int|None]=col(Integer)
    kg:Mapped[Decimal|None]=col(Quantity)
    cbm:Mapped[Decimal|None]=col(Quantity)
    freight_usd:Mapped[Decimal|None]=col(Money)
    clearing_ngn:Mapped[Decimal|None]=col(Money)
    ngn_per_usd:Mapped[Decimal|None]=col(Quantity)
    pricing_authority:Mapped[str]=col(String(12),default='pending')
    quote_id:Mapped[str|None]=col(ForeignKey('quote_snapshots.id'))
    invoice_id:Mapped[str|None]=col(ForeignKey('invoices.id'),index=True)
    billing_group_id:Mapped[str|None]=col(ForeignKey('native_billing_groups.id'),index=True)
    state:Mapped[str]=col(String(20),default='active')
    review_state:Mapped[str]=col(String(20),default='pending')


class NativeBillingGroup(Record,Base):
    __tablename__='native_billing_groups'
    document_id:Mapped[str]=col(ForeignKey('packing_documents.id'),index=True)
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    party_id:Mapped[str|None]=col(ForeignKey('parties.id'))
    invoice_id:Mapped[str|None]=col(ForeignKey('invoices.id'),unique=True)
    state:Mapped[str]=col(String(20),default='active')


class NativeGroupRevision(Record,Base):
    __tablename__='native_group_revisions'
    document_id:Mapped[str]=col(ForeignKey('packing_documents.id'),index=True)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    reason:Mapped[str]=col(Text)
    before:Mapped[dict]=col(JSONB)
    after:Mapped[dict]=col(JSONB)


class CargoSourceRow(Record,Base):
    __tablename__='cargo_source_rows'
    __table_args__=(UniqueConstraint('batch_id','sheet','row_number'),)
    batch_id:Mapped[str]=col(ForeignKey('cargo_import_batches.id'),index=True)
    sheet:Mapped[str]=col(String(200))
    row_number:Mapped[int]=col(Integer)
    position:Mapped[int]=col(Integer)
    raw:Mapped[list]=col(JSONB)
    provenance:Mapped[dict]=col(JSONB)
    issues:Mapped[list]=col(JSONB)
    eligible:Mapped[bool]=col(Boolean)
    disposition:Mapped[str]=col(String(16),default='pending')
    exclusion_reason:Mapped[str|None]=col(Text)
    reviewed_by:Mapped[str|None]=col(ForeignKey('users.id'))
    receipt:Mapped[str|None]=col(String(200))
    mark:Mapped[str|None]=col(String(300))
    item:Mapped[str|None]=col(Text)
    contact_raw:Mapped[str|None]=col(Text)
    destination:Mapped[str|None]=col(String(200))
    storage_location:Mapped[str|None]=col(String(200))
    address:Mapped[str|None]=col(Text)
    salesperson:Mapped[str|None]=col(String(200))
    operator_name:Mapped[str|None]=col(String(200))
    received_on:Mapped[date|None]=col(Date)
    pieces:Mapped[int|None]=col(Integer)
    kg:Mapped[Decimal|None]=col(Quantity)
    cbm:Mapped[Decimal|None]=col(Quantity)
    freight_usd:Mapped[Decimal|None]=col(Money)
    clearing_ngn:Mapped[Decimal|None]=col(Money)
    ticket_id:Mapped[str|None]=col(ForeignKey('tickets.id'))
    native_receipt_id:Mapped[str|None]=col(ForeignKey('receipts.id'))


class DocumentRevision(Record,Base):
    __tablename__='document_revisions'
    document_id:Mapped[str]=col(ForeignKey('packing_documents.id'),index=True)
    batch_id:Mapped[str]=col(ForeignKey('cargo_import_batches.id'))
    target_version:Mapped[int]=col(Integer)
    mapping:Mapped[dict]=col(JSONB)
    difference:Mapped[dict]=col(JSONB)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))


class Arrival(Record,Base):
    __tablename__='cargo_arrivals'
    ticket_id:Mapped[str]=col(ForeignKey('tickets.id'),index=True)
    occurred_on:Mapped[date]=col(Date)
    pieces:Mapped[int]=col(Integer)
    kg:Mapped[Decimal|None]=col(Quantity)
    cbm:Mapped[Decimal]=col(Quantity)
    freight_usd:Mapped[Decimal|None]=col(Money)
    clearing_ngn:Mapped[Decimal|None]=col(Money)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    source:Mapped[str]=col(Text)
    charge_basis:Mapped[list|None]=col(JSONB)


class ArrivalDispatch(Record,Base):
    __tablename__='arrival_dispatch_allocations'
    __table_args__=(UniqueConstraint('arrival_id','stock_event_id'),CheckConstraint('pieces > 0 AND kg >= 0 AND cbm >= 0'))
    arrival_id:Mapped[str]=col(ForeignKey('cargo_arrivals.id'),index=True)
    stock_event_id:Mapped[str]=col(ForeignKey('stock_events.id'),index=True)
    source_version:Mapped[int]=col(Integer)
    receipt_id:Mapped[str]=col(ForeignKey('receipts.id'))
    pieces:Mapped[int]=col(Integer)
    kg:Mapped[Decimal]=col(Quantity)
    cbm:Mapped[Decimal]=col(Quantity)


class CargoChargeComponent(Record,Base):
    """Immutable source-charge lines; a revision replaces the effective set, never history."""
    __tablename__='cargo_charge_components'
    __table_args__=(
        CheckConstraint('(quote_id IS NULL) <> (ticket_id IS NULL)'),
        CheckConstraint("component IN ('freight','clearing') AND currency IN ('USD','NGN') AND unit IN ('PCS','KG','CBM')"),
        CheckConstraint('amount >= 0'),
        UniqueConstraint('ticket_id','ticket_version','position'),
        UniqueConstraint('quote_id','position'),
    )
    quote_id:Mapped[str|None]=col(ForeignKey('quote_snapshots.id'))
    ticket_id:Mapped[str|None]=col(ForeignKey('tickets.id'))
    ticket_version:Mapped[int|None]=col(Integer)
    position:Mapped[int]=col(Integer)
    component:Mapped[str]=col(String(12))
    currency:Mapped[str]=col(String(6))
    unit:Mapped[str]=col(String(6))
    amount:Mapped[Decimal|None]=col(Money)
    source:Mapped[str]=col(Text)


class TicketAttachment(Record,Base):
    __tablename__='ticket_attachments'
    __table_args__=(UniqueConstraint('ticket_id','attachment_id'),)
    ticket_id:Mapped[str]=col(ForeignKey('tickets.id'))
    attachment_id:Mapped[str]=col(ForeignKey('attachments.id'))


class TicketReview(Record,Base):
    __tablename__='ticket_reviews'
    ticket_id:Mapped[str]=col(ForeignKey('tickets.id'))
    target_version:Mapped[int]=col(Integer)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    decision:Mapped[str]=col(String(20))


class ArrivalSettlement(Record,Base):
    __tablename__='arrival_settlements'
    __table_args__=(UniqueConstraint('invoice_id','invoice_version','arrival_id'),)
    invoice_id:Mapped[str]=col(ForeignKey('invoices.id'),index=True)
    invoice_version:Mapped[int]=col(Integer)
    ticket_id:Mapped[str]=col(ForeignKey('tickets.id'))
    ticket_version:Mapped[int]=col(Integer)
    arrival_id:Mapped[str]=col(ForeignKey('cargo_arrivals.id'))
    arrival_version:Mapped[int]=col(Integer)
    freight_usd:Mapped[Decimal]=col(Money)
    charge_ngn:Mapped[Decimal]=col(Money)
    ngn_per_usd:Mapped[Decimal|None]=col(Quantity)
    settled_ngn:Mapped[Decimal]=col(Money)
    original_total_ngn:Mapped[Decimal]=col(Money)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
