from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    id: str
    capabilities: set[str]
    warehouse_ids: set[str]


class DomainError(Exception):
    def __init__(self, code, message, status=400, details=None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


def require(actor: Actor, capability: str, warehouse_id=None):
    if '*' in actor.capabilities:
        return
    if capability not in actor.capabilities or (warehouse_id is not None and warehouse_id not in actor.warehouse_ids):
        raise DomainError('forbidden', 'Permission denied', 403)


def check_version(entity, expected):
    if expected is None or entity.version != expected:
        raise DomainError('stale_version', 'Record changed; reload before saving', 409)


def json_value(value):
    from datetime import datetime, date
    from decimal import Decimal
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


def audit(db, actor, action, entity_type, entity_id, before, after):
    from app.models import AuditEvent
    db.add(AuditEvent(actor_id=actor.id, action=action, entity_type=entity_type,
        entity_id=str(entity_id), before=json_value(before), after=json_value(after)))


def run_command(db, actor, command, key, payload, handler):
    from app.migration.operations import writer_gate
    writer_gate(db)
    import hashlib
    import json
    from sqlalchemy import select, text
    from argon2 import PasswordHasher
    from argon2.exceptions import VerificationError, InvalidHashError
    from app.models import Command
    if not key or len(key) > 200:
        raise DomainError('idempotency_required', 'Provide Idempotency-Key', 400)
    digest = hashlib.sha256(json.dumps(json_value(payload), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    lock = int.from_bytes(hashlib.sha256(f'{actor.id}:{command}:{key}'.encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
    existing = db.scalar(select(Command).where(Command.actor_id == actor.id, Command.command == command, Command.key == key))
    if existing:
        prefix = 'argon2id-sha256-v1:'
        if not existing.request_hash.startswith(prefix):
            raise DomainError('fingerprint_upgrade_required', 'Command fingerprint requires migration', 503)
        try:
            PasswordHasher().verify(existing.request_hash[len(prefix):], digest)
        except (VerificationError, InvalidHashError):
            raise DomainError('idempotency_conflict', 'Key already used with a different request', 409)
        return existing.result
    result = json_value(handler())
    fingerprint = 'argon2id-sha256-v1:' + PasswordHasher().hash(digest)
    db.add(Command(actor_id=actor.id, command=command, key=key, request_hash=fingerprint, result=result))
    db.flush()
    return result
