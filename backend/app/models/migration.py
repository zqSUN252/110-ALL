"""Typed historical controls. NULL links mean unresolved ownership, never fake parents."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import String, Text, Numeric, ForeignKey, Integer, Boolean, Date, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column as col
from app.db import Base
from app.models import Record


class BankIdentityDecision(Record, Base):
    __tablename__='bank_identity_decisions'
    __table_args__=(UniqueConstraint('left_id','right_id'),)
    left_id:Mapped[str]=col(ForeignKey('bank_entries.id'))
    right_id:Mapped[str]=col(ForeignKey('bank_entries.id'))
    decision:Mapped[str]=col(String(20))
    basis:Mapped[dict]=col(JSONB)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    source:Mapped[str]=col(Text)
    reason:Mapped[str]=col(Text)


class LegacyBillApplication(Record, Base):
    __tablename__='legacy_bill_applications'
    bill_id:Mapped[str]=col(ForeignKey('legacy_bills.id'),unique=True)
    historical_owner_party_id:Mapped[str]=col(ForeignKey('parties.id'))
    applied_received:Mapped[Decimal]=col(Numeric(20,2))
    applied_discount:Mapped[Decimal]=col(Numeric(20,2))
    state:Mapped[str]=col(String(30),default='active')


class LegacyGroupRevision(Record, Base):
    __tablename__='legacy_group_revisions'
    bill_id:Mapped[str]=col(ForeignKey('legacy_bills.id'),index=True)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    action:Mapped[str]=col(String(30))
    source:Mapped[str]=col(Text)
    reason:Mapped[str]=col(Text)
    before:Mapped[dict]=col(JSONB)
    after:Mapped[dict]=col(JSONB)
    preview:Mapped[dict]=col(JSONB)


class RecoveryCheckpoint(Record, Base):
    __tablename__='recovery_checkpoints'
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    source:Mapped[str]=col(Text)
    cutoff:Mapped[datetime]=col(DateTime(timezone=True))
    state:Mapped[str]=col(String(30),default='captured')
    facts:Mapped[dict]=col(JSONB)
    writer:Mapped[dict]=col(JSONB)


class MigrationBatch(Record, Base):
    __tablename__ = 'migration_batches'
    __table_args__ = (UniqueConstraint('dataset','sha256'),)
    dataset: Mapped[str] = col(String(120))
    sha256: Mapped[str] = col(String(64))
    archive_sha256: Mapped[str | None] = col(String(64))
    source_version: Mapped[str] = col(String(100))
    classification: Mapped[str] = col(String(30))
    state: Mapped[str] = col(String(30))
    plan_hash: Mapped[str] = col(String(64))
    controls: Mapped[dict] = col(JSONB)
    source_reference: Mapped[dict] = col(JSONB)


class LegacySource:
    dataset: Mapped[str] = col(String(120), index=True)
    source_key: Mapped[str] = col(String(250))
    batch_id: Mapped[str] = col(ForeignKey('migration_batches.id'))
    pointer: Mapped[str] = col(Text)
    raw: Mapped[dict] = col(JSONB)
    state: Mapped[str] = col(String(30), default='unresolved')


class LegacyParty(LegacySource, Record, Base):
    __tablename__ = 'legacy_parties'
    __table_args__ = (UniqueConstraint('dataset','source_key'),)
    name: Mapped[str] = col(Text)
    phone: Mapped[str | None] = col(Text)
    address: Mapped[str | None] = col(Text)
    classification: Mapped[str | None] = col(Text)
    native_id: Mapped[str | None] = col(ForeignKey('parties.id'), unique=True)


class LegacyShipment(LegacySource, Record, Base):
    __tablename__ = 'legacy_shipments'
    __table_args__ = (UniqueConstraint('dataset','source_key'),)
    source_no: Mapped[str] = col(Text)
    source_bl: Mapped[str | None] = col(Text)
    physical_reference: Mapped[str | None] = col(Text)
    transport: Mapped[str] = col(String(12))
    destination: Mapped[str | None] = col(Text)
    source_status: Mapped[str | None] = col(Text)
    native_id: Mapped[str | None] = col(ForeignKey('shipments.id'), unique=True)


class LegacyTicket(LegacySource, Record, Base):
    __tablename__ = 'legacy_tickets'
    __table_args__ = (UniqueConstraint('dataset','source_key'),)
    shipment_id: Mapped[str] = col(ForeignKey('legacy_shipments.id'), index=True)
    position: Mapped[int] = col(Integer)
    receipt: Mapped[str | None] = col(Text)
    consignee: Mapped[str | None] = col(Text)
    contact: Mapped[str | None] = col(Text)
    description: Mapped[str | None] = col(Text)
    destination: Mapped[str | None] = col(Text)
    pieces: Mapped[int | None] = col(Integer)
    kg: Mapped[Decimal | None] = col(Numeric(30,10))
    cbm: Mapped[Decimal | None] = col(Numeric(30,10))
    freight_usd: Mapped[Decimal | None] = col(Numeric(30,10))
    clearing_ngn: Mapped[Decimal | None] = col(Numeric(30,10))
    native_id: Mapped[str | None] = col(ForeignKey('tickets.id'), unique=True)


class LegacyBill(LegacySource, Record, Base):
    __tablename__ = 'legacy_bills'
    __table_args__ = (UniqueConstraint('dataset','source_key'),)
    kind: Mapped[str] = col(String(8))
    reference: Mapped[str] = col(Text)
    currency: Mapped[str | None] = col(String(12))
    amount: Mapped[Decimal] = col(Numeric(38,12))
    received: Mapped[Decimal] = col(Numeric(38,12))
    discount: Mapped[Decimal] = col(Numeric(38,12))
    source_status: Mapped[str] = col(String(80))
    source_party_ref: Mapped[str | None] = col(Text)
    source_shipment_ref: Mapped[str | None] = col(Text)
    party_id: Mapped[str | None] = col(ForeignKey('legacy_parties.id'))
    shipment_id: Mapped[str | None] = col(ForeignKey('legacy_shipments.id'))
    relation_state: Mapped[str] = col(String(40))
    native_id: Mapped[str | None] = col(ForeignKey('invoices.id'), unique=True)


class LegacyBillMember(Record, Base):
    __tablename__ = 'legacy_bill_members'
    __table_args__ = (UniqueConstraint('bill_id','ticket_id'),)
    bill_id: Mapped[str] = col(ForeignKey('legacy_bills.id'), index=True)
    ticket_id: Mapped[str] = col(ForeignKey('legacy_tickets.id'), index=True)
    source: Mapped[str] = col(Text)


class LegacyPaymentObservation(LegacySource, Record, Base):
    __tablename__ = 'legacy_payment_observations'
    __table_args__ = (UniqueConstraint('dataset','source_key'),)
    kind: Mapped[str] = col(String(12))
    bill_id: Mapped[str | None] = col(ForeignKey('legacy_bills.id'))
    uid: Mapped[str | None] = col(Text)
    amount_ngn: Mapped[Decimal | None] = col(Numeric(38,12))
    original_currency: Mapped[str | None] = col(String(12))
    original_amount: Mapped[Decimal | None] = col(Numeric(38,12))
    approved: Mapped[bool | None] = col(Boolean)
    approved_at: Mapped[datetime | None] = col(DateTime(timezone=True))
    approval_time_basis: Mapped[str | None] = col(String(60))
    approved_by_text: Mapped[str | None] = col(Text)
    payment_id: Mapped[str | None] = col(ForeignKey('payments.id'))
    attachment_id: Mapped[str | None] = col(ForeignKey('attachments.id'))


class LegacyF6(LegacySource, Record, Base):
    __tablename__ = 'legacy_f6'
    __table_args__ = (UniqueConstraint('dataset','source_key'),)
    receipt: Mapped[str | None] = col(Text)
    customer: Mapped[str | None] = col(Text)
    description: Mapped[str | None] = col(Text)
    contact: Mapped[str | None] = col(Text)
    warehouse_text: Mapped[str | None] = col(Text)
    destination: Mapped[str | None] = col(Text)
    pieces: Mapped[int | None] = col(Integer)
    kg: Mapped[Decimal | None] = col(Numeric(30,10))
    cbm: Mapped[Decimal | None] = col(Numeric(30,10))
    source_status: Mapped[str | None] = col(Text)
    copied_at: Mapped[datetime | None] = col(DateTime(timezone=True))
    copied_by_text: Mapped[str | None] = col(Text)


class MigrationSourceMap(Record, Base):
    __tablename__ = 'migration_source_maps'
    __table_args__ = (UniqueConstraint('batch_id','pointer'),)
    batch_id: Mapped[str] = col(ForeignKey('migration_batches.id'), index=True)
    pointer: Mapped[str] = col(Text)
    target_type: Mapped[str] = col(String(100))
    target_id: Mapped[str | None] = col(String(36))
    disposition: Mapped[str] = col(String(40))
    value_hash: Mapped[str] = col(String(64))


class MigrationIssue(Record, Base):
    __tablename__ = 'migration_issues'
    batch_id: Mapped[str] = col(ForeignKey('migration_batches.id'), index=True)
    pointer: Mapped[str] = col(Text)
    code: Mapped[str] = col(String(100))
    state: Mapped[str] = col(String(30), default='pending')
    resolution: Mapped[dict | None] = col(JSONB)


class LegacyArchive(LegacySource, Record, Base):
    __tablename__ = 'legacy_archive'
    __table_args__ = (UniqueConstraint('dataset','source_key'),)
    kind: Mapped[str] = col(String(100))


class MigrationFile(Record, Base):
    __tablename__ = 'migration_files'
    __table_args__ = (UniqueConstraint('batch_id','pointer'),)
    batch_id: Mapped[str] = col(ForeignKey('migration_batches.id'), index=True)
    pointer: Mapped[str] = col(Text)
    attachment_id: Mapped[str] = col(ForeignKey('attachments.id'))


class LegacyResolution(Record, Base):
    __tablename__ = 'legacy_resolutions'
    entity_type: Mapped[str] = col(String(100))
    entity_id: Mapped[str] = col(String(36))
    source_version: Mapped[int] = col(Integer)
    actor_id: Mapped[str] = col(ForeignKey('users.id'))
    source: Mapped[str] = col(Text)
    links: Mapped[dict] = col(JSONB)


class LegacyBankEvidence(Record,Base):
    __tablename__='legacy_bank_evidence'
    claim_id:Mapped[str]=col(ForeignKey('bank_claims.id'),unique=True)
    account_id:Mapped[str]=col(ForeignKey('bank_accounts.id'))
    control_owner_party_id:Mapped[str]=col(ForeignKey('parties.id'))
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    scope:Mapped[str]=col(String(12))
    state:Mapped[str]=col(String(30),default='linked')
    reason:Mapped[str]=col(Text)
    source:Mapped[str]=col(Text)
    basis:Mapped[dict]=col(JSONB)
    cross_party:Mapped[dict|None]=col(JSONB)


class LegacyBankCoverage(Record,Base):
    __tablename__='legacy_bank_coverage'
    __table_args__=(UniqueConstraint('evidence_id','bill_id'),)
    evidence_id:Mapped[str]=col(ForeignKey('legacy_bank_evidence.id'),index=True)
    bill_id:Mapped[str]=col(ForeignKey('legacy_bills.id'),index=True)
    source_version:Mapped[int]=col(Integer)
    amount_ngn:Mapped[Decimal]=col(Numeric(20,2))
    source:Mapped[str]=col(Text)
