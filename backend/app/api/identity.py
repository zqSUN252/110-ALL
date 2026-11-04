from datetime import timedelta
import secrets
import hashlib
import hmac
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, delete, text
from app.api.dependencies import Database, get_actor, mutate, token_hash
from app.common import Actor, DomainError, require, check_version, audit, run_command
from app.models import User, Grant, WarehouseScope, Session, AuditEvent, AppSetting, utcnow

router = APIRouter(prefix='/api')
hasher = PasswordHasher()
# Equal-cost password verification for unknown usernames.
dummy_hash = hasher.hash(secrets.token_urlsafe(24))


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Input):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=1024)


class UserCreate(Login):
    password: str = Field(min_length=12, max_length=1024)


class UserUpdate(Input):
    expected_version: int = Field(ge=1)
    username: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=1024)


class Grants(Input):
    expected_version: int = Field(ge=1)
    capabilities: list[str] = Field(max_length=200)
    warehouse_ids: list[str] = Field(max_length=200)


class SettingInput(Input):
    expected_version: int = Field(ge=0)
    value: dict


class VersionInput(Input):
    expected_version: int = Field(ge=1)


def user_json(user):
    return {'id': user.id, 'username': user.username, 'active': user.active, 'version': user.version}


def locked_user(db, user_id):
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user:
        raise DomainError('not_found', 'User not found', 404)
    return user


def command(request, db, actor, name, payload, handler):
    return run_command(db, actor, name, request.headers.get('Idempotency-Key'), payload, handler)


@router.post('/auth/login')
def login(payload: Login, request: Request, response: Response, db: Database):
    # JSON-only login prevents cross-origin form login; browser writes require CSRF afterwards.
    user = db.scalar(select(User).where(User.username == payload.username))
    try:
        hasher.verify(user.password_hash if user else dummy_hash, payload.password)
    except (VerificationError, InvalidHashError):
        raise DomainError('invalid_credentials', 'Incorrect username or password', 401)
    if not user:
        raise DomainError('invalid_credentials', 'Incorrect username or password', 401)
    if not user.active:
        raise DomainError('inactive_account', 'Account is inactive', 403)
    token = secrets.token_urlsafe(48)
    csrf = hmac.new(token.encode(), b'mcstar-browser-csrf-v1', hashlib.sha256).hexdigest()
    db.add(Session(user_id=user.id, token_hash=token_hash(token), csrf_hash=token_hash(csrf),
        expires_at=utcnow() + timedelta(hours=request.app.state.settings.session_hours)))
    response.set_cookie('mcstar_session', token, httponly=True,
        secure=request.app.state.settings.secure_cookies, samesite='strict',
        max_age=request.app.state.settings.session_hours * 3600, path='/')
    audit(db, Actor(user.id, set(), set()), 'auth.login', 'user', user.id, None, None)
    return {'user': user_json(user), 'csrf_token': csrf}


@router.get('/auth/session')
def browser_session(request: Request, response: Response, db: Database, actor=Depends(get_actor)):
    # A same-origin read recovers a stable, session-bound CSRF value after reload.
    # The HttpOnly authentication token is never returned. No CORS is enabled.
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        raise DomainError('csrf', 'Same-origin session request required', 403)
    token = request.cookies['mcstar_session']
    csrf = hmac.new(token.encode(), b'mcstar-browser-csrf-v1', hashlib.sha256).hexdigest()
    request.state.session.csrf_hash = token_hash(csrf)
    response.headers['Cache-Control'] = 'private, no-store'
    return {'user': {**user_json(request.state.user), 'capabilities': sorted(actor.capabilities),
        'warehouse_ids': sorted(actor.warehouse_ids)}, 'csrf_token': csrf}


@router.post('/auth/logout')
def logout(request: Request, response: Response, db: Database, actor=Depends(mutate)):
    request.state.session.revoked_at = utcnow()
    request.state.session.version += 1
    audit(db, actor, 'auth.logout', 'user', actor.id, None, None)
    response.delete_cookie('mcstar_session', path='/')
    return {'ok': True}


@router.get('/me')
def me(request: Request, actor=Depends(get_actor)):
    return {**user_json(request.state.user), 'capabilities': sorted(actor.capabilities),
        'warehouse_ids': sorted(actor.warehouse_ids)}


@router.get('/admin/users')
def users(db: Database, actor=Depends(get_actor)):
    require(actor, 'users.read')
    return [user_json(user) for user in db.scalars(select(User).order_by(User.username))]


@router.post('/admin/users', status_code=201)
def create_user(payload: UserCreate, request: Request, db: Database, actor=Depends(mutate)):
    require(actor, 'users.manage')
    def apply():
        user = User(username=payload.username, password_hash=hasher.hash(payload.password))
        db.add(user)
        db.flush()
        audit(db, actor, 'users.create', 'user', user.id, None, user_json(user))
        return user_json(user)
    return command(request, db, actor, 'users.create', payload.model_dump(), apply)


@router.patch('/admin/users/{user_id}')
def update_user(user_id: str, payload: UserUpdate, request: Request, db: Database, actor=Depends(mutate)):
    require(actor, 'users.manage')
    def apply():
        user = locked_user(db, user_id)
        check_version(user, payload.expected_version)
        before = user_json(user)
        if user.id == actor.id and payload.active is False:
            raise DomainError('self_disable', 'Cannot deactivate current administrator', 409)
        for field in ('username', 'active'):
            if getattr(payload, field) is not None:
                setattr(user, field, getattr(payload, field))
        if payload.password:
            user.password_hash = hasher.hash(payload.password)
            for session in db.scalars(select(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None))):
                session.revoked_at = utcnow()
        user.version += 1
        audit(db, actor, 'users.update', 'user', user.id, before, user_json(user))
        return user_json(user)
    return command(request, db, actor, f'users.update:{user_id}', payload.model_dump(), apply)


@router.delete('/admin/users/{user_id}')
def deactivate_user(user_id: str, payload: VersionInput, request: Request, db: Database, actor=Depends(mutate)):
    # Keep identity and audit references; deletion is a reversible deactivation.
    return update_user(user_id, UserUpdate(expected_version=payload.expected_version, active=False), request, db, actor)


@router.put('/admin/users/{user_id}/grants')
def grants(user_id: str, payload: Grants, request: Request, db: Database, actor=Depends(mutate)):
    require(actor, 'users.manage')
    if any(not item or len(item) > 120 for item in payload.capabilities) or any(not item or len(item) > 36 for item in payload.warehouse_ids):
        raise DomainError('invalid_grants', 'Invalid capability or warehouse ID', 422)
    if user_id == actor.id and '*' in actor.capabilities and '*' not in payload.capabilities:
        raise DomainError('self_demote', 'Cannot remove own administrator grant', 409)
    from app.models import Warehouse
    existing=set(db.scalars(select(Warehouse.id).where(Warehouse.id.in_(payload.warehouse_ids))))
    if existing!=set(payload.warehouse_ids):
        raise DomainError('unknown_warehouse','Every warehouse scope must name an existing warehouse',422)
    def apply():
        user = locked_user(db, user_id)
        check_version(user, payload.expected_version)
        before = {'capabilities': list(db.scalars(select(Grant.capability).where(Grant.user_id == user.id))),
            'warehouse_ids': list(db.scalars(select(WarehouseScope.warehouse_id).where(WarehouseScope.user_id == user.id))), 'version': user.version}
        db.execute(delete(Grant).where(Grant.user_id == user.id))
        db.execute(delete(WarehouseScope).where(WarehouseScope.user_id == user.id))
        db.add_all([Grant(user_id=user.id, capability=cap) for cap in sorted(set(payload.capabilities))])
        db.add_all([WarehouseScope(user_id=user.id, warehouse_id=wid) for wid in sorted(set(payload.warehouse_ids))])
        user.version += 1
        result = {**user_json(user), 'capabilities': sorted(set(payload.capabilities)), 'warehouse_ids': sorted(set(payload.warehouse_ids))}
        audit(db, actor, 'users.grants', 'user', user.id, before, result)
        return result
    return command(request, db, actor, f'users.grants:{user_id}', payload.model_dump(), apply)


@router.get('/admin/settings')
def settings(db: Database, actor=Depends(get_actor)):
    require(actor, 'settings.read')
    return [{'key': row.key, 'value': row.value, 'version': row.version} for row in db.scalars(select(AppSetting).order_by(AppSetting.key))]


@router.put('/admin/settings/{key}')
def set_setting(key: str, payload: SettingInput, request: Request, db: Database, actor=Depends(mutate)):
    require(actor, 'settings.manage')
    # Secrets and runtime integration switches are deployment configuration, never API settings.
    if key not in {'billing', 'display', 'business_timezone', 'company_profile'}:
        raise DomainError('unsupported_setting', 'Setting is not editable here', 422)
    def sensitive(value):
        if isinstance(value, dict):
            return any(any(word in name.lower() for word in ('password', 'secret', 'token', 'api_key', 'credential', 'database_url')) or sensitive(item)
                for name, item in value.items())
        return isinstance(value, list) and any(sensitive(item) for item in value)
    if sensitive(payload.value):
        raise DomainError('private_configuration', 'Secrets belong in controlled deployment configuration', 422)
    def apply():
        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key': 'setting:' + key})
        row = db.scalar(select(AppSetting).where(AppSetting.key == key).with_for_update())
        before = {'value': row.value, 'version': row.version} if row else None
        if row:
            check_version(row, payload.expected_version)
            row.value, row.version = payload.value, row.version + 1
        else:
            if payload.expected_version != 0:
                raise DomainError('stale_version', 'Setting does not exist', 409)
            row = AppSetting(key=key, value=payload.value)
            db.add(row)
            db.flush()
        result = {'key': key, 'value': row.value, 'version': row.version}
        audit(db, actor, 'settings.update', 'setting', row.id, before, result)
        return result
    return command(request, db, actor, f'settings.update:{key}', payload.model_dump(), apply)


@router.get('/admin/audit')
def audit_events(db: Database, limit: int = 100, actor=Depends(get_actor)):
    require(actor, 'audit.read')
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(max(1, min(limit, 500))))
    return [{'id': r.id, 'actor_id': r.actor_id, 'action': r.action, 'entity_type': r.entity_type,
        'entity_id': r.entity_id, 'before': r.before, 'after': r.after, 'created_at': r.created_at} for r in rows]
