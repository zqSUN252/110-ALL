from datetime import timedelta
from urllib.parse import urlsplit
from sqlalchemy import select,func
from app.common import DomainError,require,check_version
from app.models import Connection,ConnectionOperation,ConnectionEvent,Outbox,utcnow
from app.finance.service import locked,record
from app.dialogue.commands import command
from app.channels.service import ChannelConfig

VERIFY={'PHONE_VERIFY','WAITING_FOR_PASSKEY','WAITING_FOR_PASSWORD','SCAN_QR_CODE','SCAN_QR_CODE_MOBILE','PAIRING_CODE'}
KNOWN={'UNKNOWN','NOT_CONNECTED','STOPPED','STARTING','FAILED','WORKING'}|VERIFY


def manage(db,actor,p):require(actor,'connections.manage')


def boundary(row):return {k:getattr(row,k) for k in ('instance','session','generation','role')}


def current(row,config):
    return row.instance==config.instance and row.generation==config.generation and row.session==({'assistant':config.assistant_session,'main':config.main_session,'capture':config.capture_session}.get(row.role))


def creation_ready(config):
    try:
        target=urlsplit(config.webhook_url)
        return bool(config.webhook_key and target.scheme in ('http','https') and target.hostname and not target.username and not target.password and not target.fragment)
    except ValueError:return False


@command('connection.configure','role session instance generation expected_version recovery_enabled alert_actor_id alert_recipient',manage)
def configure(db,actor,p):
    if p['role'] not in ('main','assistant','capture'):raise DomainError('connection_role','Unknown connection role')
    if any(not isinstance(p[k],str) or not 1<=len(p[k])<=100 for k in ('session','instance','generation')):raise DomainError('connection_identity','Explicit bounded identity required')
    row=db.scalar(select(Connection).where(Connection.role==p['role']).with_for_update())
    if row:
        check_version(row,p.get('expected_version'))
        changed=any(getattr(row,k)!=p[k] for k in ('session','instance','generation'))
        if changed:
            row.status='UNKNOWN';row.ever_paired=False;row.owner_id=None;row.qr_attachment_id=None;row.qr_expires_at=None;row.status_since=None;row.observed_at=None;row.auth_blocked=False;row.transport_ok=False
            row.request_revision+=1;row.observation_revision=row.request_revision
        for k in ('session','instance','generation'):setattr(row,k,p[k])
        row.version+=1
    else:
        if p.get('expected_version')!=0:raise DomainError('stale_version','Use version zero to create',409)
        row=Connection(operator_id=actor.id,**{k:p[k] for k in ('role','session','instance','generation')});db.add(row);db.flush()
    row.operator_id=actor.id
    if 'recovery_enabled' in p:row.recovery_enabled=p['recovery_enabled']
    if 'alert_recipient' in p:
        config=ChannelConfig.from_env()
        if p['alert_recipient'] and p['alert_recipient'] not in config.recipients:raise DomainError('send_scope','Alert target must be allowlisted',403)
        if p.get('alert_actor_id') and p['alert_actor_id']!=actor.id:raise DomainError('forbidden','Notification operator must be current actor',403)
        row.alert_recipient=p['alert_recipient'];row.alert_actor_id=actor.id if row.alert_recipient else None
    return record(row)


def observe(db,row,status,revision,owner=None,now=None):
    now=now or utcnow()
    if revision<=row.observation_revision:return False
    if not isinstance(status,str) or status not in KNOWN:status='UNKNOWN'
    row.observation_revision=revision;row.request_revision=max(row.request_revision,revision)
    if row.status!=status:
        row.status_since=now;row.qr_attachment_id=None;row.qr_expires_at=None
    elif status=='SCAN_QR_CODE':row.qr_attachment_id=None;row.qr_expires_at=None
    row.status=status;row.observed_at=now;row.version+=1
    if isinstance(owner,str) and 1<=len(owner)<=250:row.owner_id=owner
    if status=='WORKING':row.ever_paired=True;row.auth_blocked=False
    elif status in VERIFY:row.auth_blocked=True
    return True


def recovery_reason(db,row,now=None):
    now=now or utcnow()
    if not row.recovery_enabled or not row.ever_paired or not row.owner_id or row.auth_blocked:return 'not_eligible'
    age=(now-row.status_since).total_seconds() if row.status_since else 0
    if not ((row.status in ('FAILED','STOPPED') and age>=60) or (row.status=='STARTING' and age>=300)):return 'not_eligible'
    attempts=list(db.scalars(select(ConnectionOperation.attempted_at).where(ConnectionOperation.connection_id==row.id,ConnectionOperation.kind.in_(['recover','connect']),ConnectionOperation.attempted_at>now-timedelta(hours=1)).order_by(ConnectionOperation.attempted_at.desc())))
    if attempts and attempts[0]>now-timedelta(minutes=10):return 'cooldown'
    if len(attempts)>=2:return 'hourly_limit'
    if not row.transport_ok or not row.transport_checked_at or row.transport_checked_at<now-timedelta(seconds=90):return 'transport_deferred'
    return None


@command('connection.request','id expected_version kind',manage)
def request_operation(db,actor,p):
    row=locked(db,Connection,p['id']);check_version(row,p.get('expected_version'))
    kind=p['kind'];settings=db.info['settings']
    if kind not in ('inspect','qr','connect','recover','diagnose'):raise DomainError('connection_operation','Unsupported operation')
    if not (settings.connection_diagnostics_enabled if kind=='diagnose' else settings.connection_control_enabled):raise DomainError('connection_disabled','External connection operation is disabled',409)
    config=ChannelConfig.from_env()
    if not current(row,config):raise DomainError('connection_boundary','Configured connection differs from deployment',409)
    if kind=='qr' and row.status!='SCAN_QR_CODE':raise DomainError('qr_unavailable','QR is only available while awaiting scan',409)
    if kind=='connect' and row.status not in ('STOPPED','FAILED','NOT_CONNECTED'):raise DomainError('connection_state','Manual connect is unavailable in current state',409)
    if kind=='connect' and row.status=='NOT_CONNECTED' and not creation_ready(config):raise DomainError('webhook_configuration_required','Explicit protected webhook URL/key needed before creating a session',409)
    if kind=='connect':
        recent=db.scalar(select(ConnectionOperation.id).where(ConnectionOperation.connection_id==row.id,ConnectionOperation.attempted_at>utcnow()-timedelta(seconds=30)))
        if recent:raise DomainError('cooldown','Manual connection request cooldown is 30 seconds',409)
    if kind=='recover':
        reason=recovery_reason(db,row)
        if reason:raise DomainError(reason,'Recovery deferred',409)
    active=db.scalar(select(ConnectionOperation.id).where(ConnectionOperation.connection_id==row.id,ConnectionOperation.state.in_(['pending','running'])))
    if active:raise DomainError('operation_pending','Connection operation already pending',409)
    row.request_revision+=1;row.version+=1
    op=ConnectionOperation(connection_id=row.id,actor_id=actor.id,kind=kind,boundary=boundary(row),revision=row.request_revision)
    db.add(op);db.flush();return record(op)


def view(db,actor):
    require(actor,'connections.read')
    return [record(row)|{'qr_available':bool(row.status=='SCAN_QR_CODE' and row.qr_expires_at and row.qr_expires_at>utcnow()),'online':row.status=='WORKING' and bool(row.observed_at and row.observed_at>utcnow()-timedelta(seconds=90)),'diagnostic':('phone_action_required' if row.status in VERIFY-{'SCAN_QR_CODE'} else 'unpaired' if not row.ever_paired else 'observed')} for row in db.scalars(select(Connection).order_by(Connection.role))]


def alerts(db,row,now=None):
    now=now or utcnow();age=(now-row.status_since).total_seconds() if row.status_since else 0
    kind=None
    if row.status!='WORKING' and age>=60 and not row.offline_announced:kind='offline';row.offline_announced=True
    if row.status=='WORKING' and age>=20 and row.offline_announced:kind='recovered';row.offline_announced=False
    if not kind:return
    event=ConnectionEvent(connection_id=row.id,kind=kind);db.add(event);db.flush()
    out=Outbox(topic='connection.alert',payload={'event_id':event.id,'actor_id':row.alert_actor_id,'recipient':row.alert_recipient,'boundary':boundary(row)},status='pending' if row.alert_actor_id and row.alert_recipient else 'unconfigured',idempotency_key='connection.alert:'+event.id)
    db.add(out);db.flush();event.outbox_id=out.id
