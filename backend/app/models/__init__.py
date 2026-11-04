"""Infrastructure models and explicit domain model registry."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import String, Text, Boolean, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class User(Record, Base):
    __tablename__ = 'users'
    username: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Grant(Record, Base):
    __tablename__ = 'grants'
    __table_args__ = (UniqueConstraint('user_id', 'capability'),)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    capability: Mapped[str] = mapped_column(String(120), nullable=False)


class WarehouseScope(Record, Base):
    __tablename__ = 'warehouse_scopes'
    __table_args__ = (UniqueConstraint('user_id', 'warehouse_id'),)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    warehouse_id: Mapped[str] = mapped_column(String(36), nullable=False)


class Session(Record, Base):
    __tablename__ = 'sessions'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Record, Base):
    __tablename__ = 'audit_events'
    actor_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(120), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)


class Command(Record, Base):
    __tablename__ = 'commands'
    __table_args__ = (UniqueConstraint('actor_id', 'command', 'key'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False)
    command: Mapped[str] = mapped_column(String(120), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)


class Attachment(Record, Base):
    __tablename__ = 'attachments'
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_path: Mapped[str] = mapped_column(String(160), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(120), nullable=False)


class AppSetting(Record, Base):
    __tablename__ = 'app_settings'
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)


class Job(Record, Base):
    __tablename__ = 'jobs'
    kind: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default='pending', nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)


class Outbox(Record, Base):
    __tablename__ = 'outbox'
    topic: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default='pending', nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)

from .finance import (Warehouse, Party, Invoice, InvoiceAdjustment, Discount, Payment, Allocation, OpeningBalance, EvidenceCase, EvidenceImage, EvidenceReview, RecognitionJob, BankAccount, BankFile, BankEntry, BankSourceRow, BankClaim, BankMatch, ChannelMessage, ChannelAction, Delivery, FinanceEvent)
