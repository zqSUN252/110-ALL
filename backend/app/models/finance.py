"""Typed financial and evidence records. JSON columns retain source/candidate snapshots only."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import String, Text, Numeric, Date, DateTime, ForeignKey, Integer, Boolean, UniqueConstraint, Index, CheckConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column as col
from app.db import Base
from app.models import Record

Money = Numeric(20, 2)


class Warehouse(Record, Base):
    __tablename__ = 'warehouses'
    name: Mapped[str] = col(String(200))
    address: Mapped[str | None] = col(Text)
    contact: Mapped[str | None] = col(Text)
    source: Mapped[str | None] = col(Text)


class Party(Record, Base):
    __tablename__ = 'parties'
    warehouse_id: Mapped[str] = col(ForeignKey('warehouses.id'), index=True)
    name: Mapped[str] = col(String(200))
    classification: Mapped[str | None] = col(String(120))
    phone: Mapped[str | None] = col(String(120))
    address: Mapped[str | None] = col(Text)
    source: Mapped[str | None] = col(Text)


class Invoice(Record, Base):
    __tablename__ = 'invoices'
    __table_args__ = (CheckConstraint("amount_ngn >= 0"), Index('ix_invoice_scope_party', 'warehouse_id', 'scope', 'party_id', 'kind', 'due_on'))
    warehouse_id: Mapped[str] = col(ForeignKey('warehouses.id'))
    party_id: Mapped[str] = col(ForeignKey('parties.id'))
    scope: Mapped[str] = col(String(12), default='live')
    kind: Mapped[str] = col(String(8), default='AR')
    reference: Mapped[str] = col(String(200))
    amount_ngn: Mapped[Decimal] = col(Money)
    charge_usd: Mapped[Decimal | None] = col(Money)
    charge_ngn: Mapped[Decimal | None] = col(Money)
    ngn_per_usd: Mapped[Decimal | None] = col(Numeric(20,6))
    uploaded_on: Mapped[date] = col(Date)
    due_on: Mapped[date] = col(Date)
    period: Mapped[str | None] = col(String(100))
    source_key: Mapped[str | None] = col(String(200), unique=True)
    pricing: Mapped[dict] = col(JSONB, default=dict)
    state: Mapped[str] = col(String(30), default='open')


class InvoiceAdjustment(Record, Base):
    __tablename__ = 'invoice_adjustments'
    invoice_id: Mapped[str] = col(ForeignKey('invoices.id'), index=True)
    old_amount: Mapped[Decimal] = col(Money)
    new_amount: Mapped[Decimal] = col(Money)
    reason: Mapped[str] = col(Text)
    actor_id: Mapped[str] = col(ForeignKey('users.id'))


class Discount(Record, Base):
    __tablename__ = 'discounts'
    __table_args__ = (CheckConstraint('amount_ngn <> 0'),)
    invoice_id: Mapped[str] = col(ForeignKey('invoices.id'), index=True)
    amount_ngn: Mapped[Decimal] = col(Money)
    reason: Mapped[str | None] = col(Text)
    actor_id: Mapped[str] = col(ForeignKey('users.id'))


class Payment(Record, Base):
    __tablename__ = 'payments'
    __table_args__ = (CheckConstraint('amount > 0 AND amount_ngn > 0'), Index('ix_payment_scope_party', 'warehouse_id','scope','party_id','approved_at'))
    warehouse_id: Mapped[str] = col(ForeignKey('warehouses.id'))
    party_id: Mapped[str] = col(ForeignKey('parties.id'))
    scope: Mapped[str] = col(String(12), default='live')
    direction: Mapped[str] = col(String(8), default='in')
    amount: Mapped[Decimal | None] = col(Money)
    currency: Mapped[str | None] = col(String(3))
    amount_ngn: Mapped[Decimal] = col(Money)
    fx_direction: Mapped[str] = col(String(30))
    fx_rate: Mapped[Decimal | None] = col(Numeric(20,6))
    ngn_per_usd: Mapped[Decimal | None] = col(Numeric(20,6))
    cny_per_usd: Mapped[Decimal | None] = col(Numeric(20,6))
    paid_on: Mapped[date | None] = col(Date)
    approved_at: Mapped[datetime | None] = col(DateTime(timezone=True))
    approved_by: Mapped[str | None] = col(ForeignKey('users.id'))
    approval: Mapped[str | None] = col(String(30))
    state: Mapped[str] = col(String(30), default='pending')
    bank_state: Mapped[str] = col(String(30), default='unverified')
    period: Mapped[str | None] = col(String(100))
    source_key: Mapped[str | None] = col(String(200), unique=True)


class Allocation(Record, Base):
    __tablename__ = 'allocations'
    __table_args__ = (CheckConstraint('amount_ngn <> 0'),)
    payment_id: Mapped[str] = col(ForeignKey('payments.id'), index=True)
    invoice_id: Mapped[str] = col(ForeignKey('invoices.id'), index=True)
    amount_ngn: Mapped[Decimal] = col(Money)
    reverses_id: Mapped[str | None] = col(ForeignKey('allocations.id'))
    reason: Mapped[str | None] = col(Text)
    actor_id: Mapped[str] = col(ForeignKey('users.id'))


class OpeningBalance(Record, Base):
    __tablename__ = 'opening_balances'
    party_id: Mapped[str] = col(ForeignKey('parties.id'))
    warehouse_id: Mapped[str] = col(ForeignKey('warehouses.id'))
    scope: Mapped[str] = col(String(12))
    kind: Mapped[str] = col(String(12))
    amount_ngn: Mapped[Decimal] = col(Money)
    as_of: Mapped[date] = col(Date)
    source: Mapped[str] = col(Text)
    invoice_id: Mapped[str | None] = col(ForeignKey('invoices.id'))
    payment_id: Mapped[str | None] = col(ForeignKey('payments.id'))


class EvidenceCase(Record, Base):
    __tablename__ = 'evidence_cases'
    warehouse_id: Mapped[str] = col(ForeignKey('warehouses.id'))
    scope: Mapped[str] = col(String(12), default='live')
    payment_id: Mapped[str | None] = col(ForeignKey('payments.id'), index=True)
    account_id: Mapped[str | None] = col(ForeignKey('bank_accounts.id'))
    state: Mapped[str] = col(String(30), default='pending')
    amount: Mapped[Decimal | None] = col(Money)
    currency: Mapped[str | None] = col(String(3))
    transaction_date: Mapped[date | None] = col(Date)
    payer_name: Mapped[str | None] = col(String(500))
    transaction_reference: Mapped[str | None] = col(String(500))
    recipient_account: Mapped[str | None] = col(String(120))
    bank_name: Mapped[str | None] = col(String(120))
    business_reference: Mapped[str | None] = col(String(500))
    manual_fields: Mapped[list] = col(JSONB, default=list)


class EvidenceImage(Record, Base):
    __tablename__ = 'evidence_images'
    __table_args__ = (UniqueConstraint('case_id', 'attachment_id'),)
    case_id: Mapped[str] = col(ForeignKey('evidence_cases.id'), index=True)
    attachment_id: Mapped[str] = col(ForeignKey('attachments.id'))
    message_id: Mapped[str | None] = col(ForeignKey('channel_messages.id'))
    source: Mapped[str] = col(Text)
    context: Mapped[dict] = col(JSONB, default=dict)


class EvidenceReview(Record, Base):
    __tablename__ = 'evidence_reviews'
    case_id: Mapped[str] = col(ForeignKey('evidence_cases.id'), index=True)
    actor_id: Mapped[str] = col(ForeignKey('users.id'))
    action: Mapped[str] = col(String(30))
    previous_version: Mapped[int] = col(Integer)
    fields: Mapped[dict] = col(JSONB)
    reason: Mapped[str | None] = col(Text)


class RecognitionJob(Record, Base):
    __tablename__ = 'recognition_jobs'
    image_id: Mapped[str] = col(ForeignKey('evidence_images.id'), index=True)
    actor_id: Mapped[str] = col(ForeignKey('users.id'))
    case_version: Mapped[int] = col(Integer)
    state: Mapped[str] = col(String(30), default='pending', index=True)
    stage: Mapped[str] = col(String(20), default='whole')
    cache_key: Mapped[str] = col(String(64), unique=True)
    lease_token: Mapped[str | None] = col(String(36))
    lease_until: Mapped[datetime | None] = col(DateTime(timezone=True))
    attempts: Mapped[int] = col(Integer, default=0)
    available_at: Mapped[datetime] = col(DateTime(timezone=True))
    error_code: Mapped[str | None] = col(String(100))
    result: Mapped[dict | None] = col(JSONB)


class BankAccount(Record, Base):
    __tablename__ = 'bank_accounts'
    __table_args__ = (UniqueConstraint('scope','bank_name','account_number','currency'),)
    warehouse_id: Mapped[str] = col(ForeignKey('warehouses.id'))
    scope: Mapped[str] = col(String(12), default='live')
    bank_name: Mapped[str] = col(String(120))
    account_number: Mapped[str] = col(String(120))
    currency: Mapped[str] = col(String(3))
    revision: Mapped[int] = col(Integer, default=0)


class BankFile(Record, Base):
    __tablename__ = 'bank_files'
    __table_args__ = (UniqueConstraint('account_id','sha256'),)
    account_id: Mapped[str] = col(ForeignKey('bank_accounts.id'))
    attachment_id: Mapped[str] = col(ForeignKey('attachments.id'))
    sha256: Mapped[str] = col(String(64))
    state: Mapped[str] = col(String(30), default='preview')
    mapping: Mapped[dict] = col(JSONB, default=dict)


class BankEntry(Record, Base):
    __tablename__ = 'bank_entries'
    __table_args__ = (Index('ix_bank_amount_bucket','account_id','currency','credit','transaction_date'), Index('uq_bank_trusted_id','account_id','trusted_id',unique=True,postgresql_where=text('trusted_id IS NOT NULL')))
    account_id: Mapped[str] = col(ForeignKey('bank_accounts.id'))
    currency: Mapped[str] = col(String(3))
    credit: Mapped[Decimal] = col(Money)
    debit: Mapped[Decimal] = col(Money)
    transaction_date: Mapped[date | None] = col(Date)
    trusted_id: Mapped[str | None] = col(String(500))
    reference: Mapped[str] = col(String(500))
    description: Mapped[str] = col(Text)
    fingerprint: Mapped[str] = col(String(64), index=True)
    issues: Mapped[list] = col(JSONB)


class BankSourceRow(Record, Base):
    __tablename__ = 'bank_source_rows'
    __table_args__ = (UniqueConstraint('file_id','sheet','row_number'),)
    file_id: Mapped[str] = col(ForeignKey('bank_files.id'), index=True)
    entry_id: Mapped[str | None] = col(ForeignKey('bank_entries.id'), index=True)
    sheet: Mapped[str] = col(String(200))
    row_number: Mapped[int] = col(Integer)
    raw_values: Mapped[list] = col(JSONB)
    parsed: Mapped[dict | None] = col(JSONB)


class BankClaim(Record, Base):
    __tablename__ = 'bank_claims'
    __table_args__ = (Index('uq_bank_active_claim','entry_id',unique=True,postgresql_where=text("state <> 'released'")),Index('uq_case_active_claim','case_id',unique=True,postgresql_where=text("state <> 'released'")),Index('uq_payment_active_claim','payment_id',unique=True,postgresql_where=text("state <> 'released' AND payment_id IS NOT NULL")))
    entry_id: Mapped[str] = col(ForeignKey('bank_entries.id'))
    case_id: Mapped[str] = col(ForeignKey('evidence_cases.id'))
    payment_id: Mapped[str | None] = col(ForeignKey('payments.id'))
    state: Mapped[str] = col(String(30))
    reason: Mapped[str] = col(Text)
    actor_id: Mapped[str] = col(ForeignKey('users.id'))


class BankMatch(Record, Base):
    __tablename__ = 'bank_matches'
    case_id: Mapped[str] = col(ForeignKey('evidence_cases.id'), index=True)
    case_version: Mapped[int] = col(Integer)
    bank_revision: Mapped[int] = col(Integer)
    decision: Mapped[dict] = col(JSONB)


class ChannelMessage(Record, Base):
    __tablename__ = 'channel_messages'
    __table_args__ = (UniqueConstraint('instance','session','source_id'),)
    instance: Mapped[str] = col(String(120))
    session: Mapped[str] = col(String(120))
    source_id: Mapped[str] = col(String(250))
    group_id: Mapped[str] = col(String(250))
    sender_id: Mapped[str] = col(String(250))
    sent_at: Mapped[datetime] = col(DateTime(timezone=True))
    warehouse_id: Mapped[str] = col(ForeignKey('warehouses.id'))
    scope: Mapped[str] = col(String(12))
    generation: Mapped[str] = col(String(100))
    body: Mapped[str | None] = col(Text)
    reply_to: Mapped[str | None] = col(String(250))
    media_id: Mapped[str | None] = col(String(250))
    body_hash: Mapped[str] = col(String(64))
    state: Mapped[str] = col(String(30))


class ChannelAction(Record, Base):
    __tablename__ = 'channel_actions'
    message_id: Mapped[str] = col(ForeignKey('channel_messages.id'))
    actor_id: Mapped[str] = col(ForeignKey('users.id'))
    nonce_hash: Mapped[str] = col(String(64), unique=True)
    expires_at: Mapped[datetime] = col(DateTime(timezone=True))
    action: Mapped[str] = col(String(100))
    payload: Mapped[dict] = col(JSONB)
    used_at: Mapped[datetime | None] = col(DateTime(timezone=True))


class Delivery(Record, Base):
    __tablename__ = 'channel_deliveries'
    message_id: Mapped[str | None] = col(ForeignKey('channel_messages.id'))
    actor_id: Mapped[str] = col(ForeignKey('users.id'))
    session: Mapped[str] = col(String(120))
    recipient: Mapped[str] = col(String(250))
    text: Mapped[str] = col(Text)
    state: Mapped[str] = col(String(30), default='pending')
    lease_until: Mapped[datetime | None] = col(DateTime(timezone=True))
    receipt: Mapped[str | None] = col(String(500))


class FinanceEvent(Record, Base):
    """Durable downstream invalidations, consumed by release/document modules."""
    __tablename__ = 'finance_events'
    invoice_id: Mapped[str | None] = col(ForeignKey('invoices.id'))
    payment_id: Mapped[str | None] = col(ForeignKey('payments.id'))
    kind: Mapped[str] = col(String(60))
    processed_at: Mapped[datetime | None] = col(DateTime(timezone=True))
