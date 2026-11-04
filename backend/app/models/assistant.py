"""Durable assistant intent, provenance, connection observations and receipts."""
from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column as col
from app.db import Base
from app.models import Record


class DialogueThread(Record,Base):
    __tablename__='dialogue_threads'
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    scope:Mapped[str]=col(String(12))
    source_message_id:Mapped[str|None]=col(ForeignKey('channel_messages.id'))
    title:Mapped[str]=col(String(200),default='')


class DialogueTurn(Record,Base):
    __tablename__='dialogue_turns'
    thread_id:Mapped[str]=col(ForeignKey('dialogue_threads.id'),index=True)
    job_id:Mapped[str]=col(ForeignKey('jobs.id'),unique=True)
    source_message_id:Mapped[str|None]=col(ForeignKey('channel_messages.id'),unique=True)
    text:Mapped[str]=col(Text)
    result:Mapped[dict|None]=col(JSONB)
    model_profile:Mapped[str|None]=col(String(64))


class DialogueFocus(Record,Base):
    __tablename__='dialogue_focus'
    __table_args__=(UniqueConstraint('thread_id','position'),)
    thread_id:Mapped[str]=col(ForeignKey('dialogue_threads.id'))
    position:Mapped[int]=col(Integer)
    case_id:Mapped[str]=col(ForeignKey('evidence_cases.id'))
    case_version:Mapped[int]=col(Integer)
    image_id:Mapped[str|None]=col(ForeignKey('evidence_images.id'))


class DialogueFile(Record,Base):
    __tablename__='dialogue_files'
    thread_id:Mapped[str]=col(ForeignKey('dialogue_threads.id'),index=True)
    turn_id:Mapped[str|None]=col(ForeignKey('dialogue_turns.id'))
    attachment_id:Mapped[str|None]=col(ForeignKey('attachments.id'))
    export_id:Mapped[str|None]=col(ForeignKey('document_exports.id'))
    notification_id:Mapped[str|None]=col(ForeignKey('notifications.id'))
    kind:Mapped[str]=col(String(40))
    sources:Mapped[dict]=col(JSONB,default=dict)


class DialogueProposal(Record,Base):
    __tablename__='dialogue_proposals'
    thread_id:Mapped[str]=col(ForeignKey('dialogue_threads.id'))
    turn_id:Mapped[str]=col(ForeignKey('dialogue_turns.id'))
    action:Mapped[str]=col(String(80))
    payload:Mapped[dict]=col(JSONB)
    nonce_hash:Mapped[str]=col(String(64))
    expires_at:Mapped[datetime]=col(DateTime(timezone=True))
    used_at:Mapped[datetime|None]=col(DateTime(timezone=True))
    result:Mapped[dict|None]=col(JSONB)


class Connection(Record,Base):
    __tablename__='connections'
    role:Mapped[str]=col(String(20),unique=True)
    instance:Mapped[str]=col(String(120))
    session:Mapped[str]=col(String(120),unique=True)
    generation:Mapped[str]=col(String(100))
    operator_id:Mapped[str]=col(ForeignKey('users.id'))
    status:Mapped[str]=col(String(50),default='UNKNOWN')
    status_since:Mapped[datetime|None]=col(DateTime(timezone=True))
    observed_at:Mapped[datetime|None]=col(DateTime(timezone=True))
    observation_revision:Mapped[int]=col(Integer,default=0)
    request_revision:Mapped[int]=col(Integer,default=0)
    ever_paired:Mapped[bool]=col(Boolean,default=False)
    owner_id:Mapped[str|None]=col(String(250))
    qr_attachment_id:Mapped[str|None]=col(ForeignKey('attachments.id'))
    qr_expires_at:Mapped[datetime|None]=col(DateTime(timezone=True))
    auth_blocked:Mapped[bool]=col(Boolean,default=False)
    transport_ok:Mapped[bool]=col(Boolean,default=False)
    transport_checked_at:Mapped[datetime|None]=col(DateTime(timezone=True))
    recovery_enabled:Mapped[bool]=col(Boolean,default=False)
    alert_actor_id:Mapped[str|None]=col(ForeignKey('users.id'))
    alert_recipient:Mapped[str|None]=col(String(250))
    offline_announced:Mapped[bool]=col(Boolean,default=False)


class ConnectionOperation(Record,Base):
    __tablename__='connection_operations'
    connection_id:Mapped[str]=col(ForeignKey('connections.id'),index=True)
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    kind:Mapped[str]=col(String(30))
    state:Mapped[str]=col(String(30),default='pending')
    boundary:Mapped[dict]=col(JSONB)
    revision:Mapped[int]=col(Integer)
    lease_until:Mapped[datetime|None]=col(DateTime(timezone=True))
    attempted_at:Mapped[datetime|None]=col(DateTime(timezone=True))
    outcome:Mapped[str|None]=col(String(60))


class ConnectionEvent(Record,Base):
    __tablename__='connection_events'
    connection_id:Mapped[str]=col(ForeignKey('connections.id'))
    kind:Mapped[str]=col(String(30))
    outbox_id:Mapped[str|None]=col(ForeignKey('outbox.id'))
    delivery_id:Mapped[str|None]=col(ForeignKey('channel_deliveries.id'))


class DeliveryReceipt(Record,Base):
    __tablename__='delivery_receipts'
    __table_args__=(UniqueConstraint('instance','session','source_id','chat_id','participant','ack'),)
    instance:Mapped[str]=col(String(120))
    session:Mapped[str]=col(String(120))
    source_id:Mapped[str]=col(String(500))
    chat_id:Mapped[str]=col(String(250))
    participant:Mapped[str]=col(String(250))
    ack:Mapped[int]=col(Integer)
    delivery_id:Mapped[str|None]=col(ForeignKey('channel_deliveries.id'))
    received_at:Mapped[datetime]=col(DateTime(timezone=True))


class ChannelReceiptObservation(Record,Base):
    __tablename__='channel_receipt_observations'
    __table_args__=(UniqueConstraint('instance','session','body_hash'),)
    instance:Mapped[str]=col(String(120))
    session:Mapped[str]=col(String(120))
    body_hash:Mapped[str]=col(String(64))
    event_type:Mapped[str]=col(String(80))
    payload:Mapped[dict]=col(JSONB)
    state:Mapped[str]=col(String(30))
    receipt_id:Mapped[str|None]=col(ForeignKey('delivery_receipts.id'))


class ChannelAutomation(Record,Base):
    __tablename__='channel_automations'
    kind:Mapped[str]=col(String(20))
    actor_id:Mapped[str]=col(ForeignKey('users.id'))
    warehouse_id:Mapped[str]=col(ForeignKey('warehouses.id'))
    scope:Mapped[str]=col(String(12))
    source_group:Mapped[str]=col(String(250))
    destination:Mapped[str]=col(String(250))
    paused:Mapped[bool]=col(Boolean,default=True)
    boundary:Mapped[dict]=col(JSONB)


class ChannelAutomationEvent(Record,Base):
    __tablename__='channel_automation_events'
    __table_args__=(UniqueConstraint('automation_id','message_id'),)
    automation_id:Mapped[str]=col(ForeignKey('channel_automations.id'))
    message_id:Mapped[str]=col(ForeignKey('channel_messages.id'))
    recognition_job_id:Mapped[str|None]=col(ForeignKey('recognition_jobs.id'))
    delivery_id:Mapped[str|None]=col(ForeignKey('channel_deliveries.id'))
    state:Mapped[str]=col(String(30),default='paused')
