"""Owned runtime generations and independently verifiable incremental backups."""
from pathlib import Path
import json
import hashlib
from app.migration.backup import read_bundle
from app.migration.source import digest
from app.private_storage import protect_private_root
from sqlalchemy import select,text
from app.models import AppSetting,AuditEvent,utcnow
from app.common import DomainError,require,check_version,audit,json_value,run_command


class DirectoryArchive:
    """Explicitly configured team/mounted destination; no implicit external target."""
    def __init__(self,root):self.root=Path(root).resolve()
    def path(self,key):
        if not __import__('re').fullmatch('[0-9a-f]{64}\\.json',key):raise ValueError('Invalid archive key')
        return self.root/key
    def write(self,key,content):
        self.root.mkdir(parents=True,exist_ok=True)
        target=self.path(key)
        if target.exists():
            if target.read_bytes()!=content:raise ValueError('Archive object conflict')
        else:
            with target.open('xb') as out:out.write(content)
    def read(self,key):return self.path(key).read_bytes()


def incremental(base,current,destination):
    bm,b=read_bundle(base);cm,c=read_bundle(current)
    if bm['revision']!=cm['revision']:raise ValueError('Incremental schema mismatch')
    destination=Path(destination)
    if destination.exists():raise ValueError('Incremental destination must be new')
    protect_private_root(destination)
    changes={}
    for name,rows in c.items():
        before={r['id']:r for r in b[name]};after={r['id']:r for r in rows}
        changes[name]={'upsert':[r for key,r in after.items() if before.get(key)!=r],'deleted':sorted(set(before)-set(after))}
    body=json.dumps(changes,ensure_ascii=False,separators=(',',':')).encode()
    (destination/'changes.json').write_bytes(body)
    new_files={k:v for k,v in cm['files'].items() if k not in bm['files']}
    for relative in new_files:
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((Path(current)/relative).read_bytes())
    manifest={'base_records_sha256':bm['records_sha256'],'changes_sha256':hashlib.sha256(body).hexdigest(),'target_manifest':cm,'files':new_files}
    (destination/'incremental.json').write_text(json.dumps(manifest),encoding='utf-8')
    return {'changed':sum(len(v['upsert']) for v in changes.values()),'deleted':sum(len(v['deleted']) for v in changes.values()),'files':len(new_files)}


def materialize(base,delta,destination):
    bm,rows=read_bundle(base);dm=json.loads((Path(delta)/'incremental.json').read_text('utf-8'))
    body=(Path(delta)/'changes.json').read_bytes()
    if bm['records_sha256']!=dm['base_records_sha256'] or hashlib.sha256(body).hexdigest()!=dm['changes_sha256']:raise ValueError('Incremental chain mismatch')
    changes=json.loads(body)
    for name,change in changes.items():
        values={r['id']:r for r in rows[name]}
        for identity in change['deleted']:values.pop(identity,None)
        values.update({r['id']:r for r in change['upsert']});rows[name]=sorted(values.values(),key=lambda r:r['id'])
    destination=Path(destination)
    if destination.exists():raise ValueError('Materialization destination must be new')
    protect_private_root(destination)
    data=json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode()
    if hashlib.sha256(data).hexdigest()!=dm['target_manifest']['records_sha256']:raise ValueError('Incremental target mismatch')
    (destination/'records.json').write_bytes(data)
    (destination/'manifest.json').write_text(json.dumps(dm['target_manifest']),encoding='utf-8')
    for relative,sha in dm['target_manifest']['files'].items():
        if relative!=f'objects/{sha[:2]}/{sha}':raise ValueError('Invalid incremental object path')
        origin=(Path(delta) if relative in dm['files'] else Path(base))/relative
        blob=origin.read_bytes()
        if hashlib.sha256(blob).hexdigest()!=sha:raise ValueError('Incremental attachment mismatch')
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(blob)
    read_bundle(destination)
    return {'verified':True}


def writer_gate(db):
    settings=db.info.get('settings')
    if settings is None:return
    row=db.scalar(select(AppSetting).where(AppSetting.key=='migration.writer').execution_options(populate_existing=True))
    if row and (row.value.get('state')!='active' or row.value.get('generation')!=settings.writer_generation):
        raise DomainError('writer_frozen','This runtime is not the current authorized writer',409)


def worker_gate(factory,settings):
    """Admission check only; a cutover also requires operators to drain in-flight IO."""
    with factory() as db,db.begin():
        db.info['settings']=settings
        writer_gate(db)


def worker_write_gate(db,settings):
    """Serialize only a short admission/publication transaction against cutover."""
    db.info['settings']=settings
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    writer_gate(db)


def cutover(db,actor,expected_version,generation,source,active=True):
    require(actor,'*')
    if not generation or not source:raise DomainError('cutover_source','Generation and frozen source handoff evidence required')
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    schema=db.execute(text('SELECT current_schema()')).scalar_one()
    if active and (schema.startswith('restore_') or db.scalar(select(AppSetting.id).where(AppSetting.key=='migration.restore_gate'))):validate_restore_gate(db)
    row=db.scalar(select(AppSetting).where(AppSetting.key=='migration.writer').with_for_update())
    if row:check_version(row,expected_version);row.version+=1
    elif expected_version!=0:raise DomainError('stale_version','Writer generation changed',409)
    else:row=AppSetting(key='migration.writer',value={});db.add(row)
    row.value={'generation':generation,'state':'active' if active else 'frozen','source':source,'cutover_at':utcnow().isoformat()}
    audit(db,actor,'migration.writer_changed','setting',row.id or 'migration.writer',None,row.value)
    db.flush();return {'version':row.version,'generation':generation,'state':row.value['state'],'external_flags_changed':False}


def fact_snapshot(db,schema=None):
    """One PostgreSQL statement snapshot, including non-audited worker/receipt facts."""
    from app.db import Base
    excluded={'recovery_checkpoints','commands','sessions','customer_sessions','phone_challenges','channel_actions','dialogue_proposals','migration_source_maps','legacy_archive'}
    selects=[]
    if schema:
        import re
        if not re.fullmatch(r'[a-z_][a-z0-9_]*',schema):raise DomainError('source_schema','Invalid controlled source schema')
    prefix=f'"{schema}".' if schema else ''
    for name in sorted(set(Base.metadata.tables)-excluded):
        where=" WHERE action NOT LIKE 'migration.%'" if name=='audit_events' else " WHERE key NOT LIKE 'migration.%'" if name=='app_settings' else " WHERE username<>'__migration_nonlogin__'" if name=='users' else ''
        selects.append(f"SELECT '{name}' AS kind,id,to_jsonb(t)::text AS value FROM {prefix}\"{name}\" t{where}")
    if hasattr(db,'flush'):db.flush()
    result={}
    for kind,identity,value in db.execute(text(' UNION ALL '.join(selects)).execution_options(yield_per=1000)):
        from decimal import Decimal
        value=json.loads(value,parse_float=Decimal)
        result.setdefault(kind,{})[identity]={'hash':digest(value),'updated_at':value.get('updated_at'),'version':value.get('version')}
    return result


def validate_restore_gate(db):
    gate=db.scalar(select(AppSetting).where(AppSetting.key=='migration.restore_gate'))
    value=gate.value if gate else {}
    if value.get('state')!='reviewed' or not value.get('drained') or not value.get('restore_verified'):
        raise DomainError('restore_reconciliation_required','Restored writer requires verified restore and current source checkpoint reconciliation',409)
    source_schema=value['source_schema']
    facts=fact_snapshot(db,source_schema)
    source_checkpoint=db.execute(text(f'SELECT version,writer FROM "{source_schema}".recovery_checkpoints WHERE id=:id'),{'id':value['checkpoint_id']}).mappings().first()
    writer=db.execute(text(f"SELECT version,value FROM \"{source_schema}\".app_settings WHERE key='migration.writer'")).mappings().first()
    current_writer={'version':writer['version'],'value':writer['value']} if writer else {'version':0,'value':None}
    if not source_checkpoint or source_checkpoint['version']!=value['checkpoint_version'] or source_checkpoint['writer']['manifest_hash']!=value['manifest_hash'] or digest(facts)!=value['facts_hash'] or current_writer!=value['source_writer']:
        raise DomainError('restore_reconciliation_required','Source facts changed after restore review; keep target frozen',409)


def backout_restore(settings,bundle,checkpoint_id,expected_version,plan,target_schema,drained=False):
    """Restore only into a new isolated schema; recheck source before and after IO."""
    from dataclasses import replace
    from sqlalchemy.engine import make_url
    from app.db import make_engine,session_factory
    from app.models import RecoveryCheckpoint
    from app.common import Actor
    from app.migration.service import importer
    from app.migration.backup import restore
    import re
    if not drained or not re.fullmatch(r'restore_[a-z0-9_]+',target_schema):raise DomainError('restore_drain_required','Explicit drained workers and owned empty restore target required')
    manifest,_=read_bundle(bundle);engine=make_engine(settings.database_url);factory=session_factory(engine)
    def check(db):
        row=db.get(RecoveryCheckpoint,checkpoint_id)
        if not row or row.version!=expected_version or row.writer['manifest_hash']!=digest(manifest):raise DomainError('checkpoint_changed','Exact original bundle/checkpoint/version required',409)
        if (writer_basis(db)['value'] or {}).get('state')!='frozen':raise DomainError('writer_freeze_required','Freeze source writer and drain before backout')
        current=backout_plan(db,row.cutoff,row.id)
        if current!=plan:raise DomainError('plan_changed','Source facts changed since backout preview',409,current)
        if current['new_events']:raise DomainError('reconciliation_required','Preserve new facts before restoring a prior cutpoint',409,current)
        return row,current
    try:
        with factory() as db,db.begin():check(db)
        with engine.begin() as connection:connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS {target_schema}'))
        target=replace(settings,database_url=make_url(settings.database_url).update_query_dict({'options':f'-csearch_path={target_schema}'}).render_as_string(hide_password=False),private_root=settings.private_root/'restores'/target_schema)
        restored=restore(target,bundle)
        with factory() as db,db.begin():
            try:row,current=check(db)
            except DomainError:return {'state':'reconciliation_required','restore_schema':target_schema,'restore_verified':True,'activated':False,'overwrite_allowed':False}
            technical=importer(db);who=Actor(technical.id,{'*'},set())
            # Importer may be newly created; its identity is technical and is not
            # used as evidence that a source bank or delivery fact never happened.
            row.state='isolated_restore_reviewed';row.version+=1;db.flush()
            gate={'state':'reviewed','restore_verified':True,'manifest_hash':digest(manifest),'records_sha256':manifest['records_sha256'],'source_schema':row.writer['schema'],
                'checkpoint_id':row.id,'checkpoint_version':row.version,'facts_hash':digest(fact_snapshot(db)),'source_writer':writer_basis(db),'drained':True}
            audit(db,who,'migration.backout_restore_reviewed','recovery_checkpoint',row.id,None,{'target_schema':target_schema,'manifest_hash':gate['manifest_hash']})
        target_engine=make_engine(target.database_url)
        try:
            with session_factory(target_engine)() as db,db.begin():
                row=db.scalar(select(AppSetting).where(AppSetting.key=='migration.restore_gate'));row.value=gate;row.version+=1
        finally:target_engine.dispose()
        return {'state':'isolated_restore_reviewed','restore_schema':target_schema,'restore_verified':restored['verified'],'activated':False,'overwrite_allowed':False}
    finally:engine.dispose()


def writer_basis(db):
    row=db.scalar(select(AppSetting).where(AppSetting.key=='migration.writer'))
    return {'version':row.version,'value':row.value} if row else {'version':0,'value':None}


def checkpoint(db,actor,source,key,manifest):
    from app.models import RecoveryCheckpoint
    require(actor,'*')
    basis=manifest.get('source_checkpoint') or {}
    schema=db.execute(text('SELECT current_schema()')).scalar_one()
    if not str(source or '').strip() or not basis or basis.get('schema')!=schema or not basis.get('cutoff') or 'facts' not in basis:
        raise DomainError('checkpoint_required','Use a verified base bundle watermark captured at the original source/schema cutpoint')
    def apply():
        from datetime import datetime
        facts=basis['facts']
        row=RecoveryCheckpoint(actor_id=actor.id,source=source,cutoff=datetime.fromisoformat(basis['cutoff']),state='captured',facts=facts,
            writer={'schema':schema,'writer':basis['writer'],'revision':manifest['revision'],'manifest_hash':digest(manifest),'records_sha256':manifest['records_sha256']})
        db.add(row);db.flush();audit(db,actor,'migration.checkpoint','recovery_checkpoint',row.id,None,{'source':source,'facts_hash':digest(facts)})
        return {'id':row.id,'version':row.version,'source':row.source,'cutoff':row.cutoff.isoformat(),'facts_hash':digest(facts)}
    return run_command(db,actor,'migration.checkpoint',key,{'source':source,'manifest_hash':digest(manifest)},apply)


def backout_plan(db,after,checkpoint_id=None):
    from app.models import RecoveryCheckpoint
    from datetime import datetime
    if after.tzinfo is None:raise DomainError('cutoff_required','Timezone-aware cutoff required')
    current=fact_snapshot(db);baseline=None
    if checkpoint_id:
        row=db.get(RecoveryCheckpoint,checkpoint_id)
        if not row or row.cutoff!=after:raise DomainError('checkpoint_changed','Exact persisted checkpoint and cutoff required',409)
        baseline=row.facts
    differences=[]
    for kind in sorted(set(current)|set(baseline or {})):
        before=(baseline or {}).get(kind,{})
        for identity,value in current.get(kind,{}).items():
            changed=value!=before.get(identity) if baseline is not None else value.get('updated_at') and datetime.fromisoformat(value['updated_at'])>after
            if changed:differences.append({'entity_type':kind,'entity_id':identity,'change':'updated_or_added','version':value['version'],'hash':value['hash']})
        if baseline is not None:
            differences += [{'entity_type':kind,'entity_id':identity,'change':'missing'} for identity in set(before)-set(current.get(kind,{}))]
    value={'cutoff':after.isoformat(),'checkpoint_id':checkpoint_id,'checkpoint_version':row.version if checkpoint_id else None,'source':row.source if checkpoint_id else None,
        'writer':writer_basis(db),'base':row.writer if checkpoint_id else None,'facts_hash':digest(current),'state':'reconciliation_required' if differences else 'no_new_business_events' if checkpoint_id else 'checkpoint_required',
        'new_events':len(differences),'events':differences,'external_facts_reversible':False,'overwrite_allowed':False,'restore_target':'new_empty_schema_only'}
    return value|{'plan_hash':digest(value)}


def confirm_backout(db,actor,p):
    from app.models import RecoveryCheckpoint
    from datetime import datetime
    require(actor,'*')
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    row=db.get(RecoveryCheckpoint,p['checkpoint_id'])
    if not row:raise DomainError('checkpoint_required','Persisted source checkpoint required')
    check_version(row,p.get('expected_version'))
    current=backout_plan(db,datetime.fromisoformat(p['cutoff']),row.id)
    if p.get('plan')!=current:raise DomainError('plan_changed','Re-read all current facts before backout',409,current)
    if current['new_events']:raise DomainError('reconciliation_required','New bank, physical, delivery or other facts must be preserved; backout cannot reverse them',409,current)
    if p.get('source')!=row.source:raise DomainError('source_changed','Exact checkpoint source required',409)
    # This approval never authorizes destructive overwrite or provider replay. A
    # separate empty restore is still verified before writer activation.
    row.state='isolated_restore_reviewed';row.version+=1
    audit(db,actor,'migration.backout_reviewed','recovery_checkpoint',row.id,None,{'plan_hash':current['plan_hash'],'overwrite_allowed':False})
    db.flush();return {'id':row.id,'version':row.version,'state':row.state,'overwrite_allowed':False,'external_facts_reversible':False}
