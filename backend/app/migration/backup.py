"""Consistent owned-DB snapshots, private files, and fail-closed empty restoration.

Original source ZIP references stay references. Sessions, OTPs, nonce authority and
external work are explicitly not executable after recovery.
"""
import hashlib
import json
from pathlib import Path
from datetime import date,datetime
from decimal import Decimal
from sqlalchemy import select,text,inspect
from sqlalchemy.sql.sqltypes import Numeric,Date,DateTime,LargeBinary
from app.db import Base,make_engine
import app.models
from app.common import json_value
from app.private_storage import protect_private_root
from app.migration.source import digest

EXCLUDED={'sessions','customer_sessions','phone_challenges','channel_actions','dialogue_proposals'}
PAUSED={'jobs':'status','outbox':'status','recognition_jobs':'state','channel_deliveries':'state','connection_operations':'state'}


def inert(name,row):
    row=dict(row)
    if name=='users':row['password_hash']='!reset-after-restore'
    state=PAUSED.get(name)
    if state and row.get(state) in ('pending','running','sending','queued','leased','retry'):
        row[state]='uncertain' if row.get(state)=='sending' else 'paused'
    if name=='connections':row['recovery_enabled']=False
    if name=='channel_automations':row['paused']=True
    return row


def backup(settings,destination):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Backup destination must be new')
    protect_private_root(destination)
    engine=make_engine(settings.database_url,isolation_level='REPEATABLE READ')
    table_rows={};files={}
    with engine.connect() as connection,connection.begin():
        connection.execute(text('SET TRANSACTION READ ONLY'))
        revision=connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        from app.migration.operations import fact_snapshot
        writer=connection.execute(text("SELECT version,value FROM app_settings WHERE key='migration.writer'")).mappings().first()
        source_checkpoint={'schema':connection.execute(text('SELECT current_schema()')).scalar_one(),
            'cutoff':connection.execute(text('SELECT transaction_timestamp()')).scalar_one().isoformat(),
            'writer':{'version':writer['version'],'value':writer['value']} if writer else {'version':0,'value':None},'facts':fact_snapshot(connection)}
        for name,table in sorted(Base.metadata.tables.items()):
            table_rows[name]=[] if name in EXCLUDED else [json_value(inert(name,dict(row))) for row in connection.execute(select(table).order_by(table.c.id)).mappings()]
        # DB transaction is a read snapshot, not the global financial guard.
        for row in table_rows['attachments']:
            relative=f'objects/{row["sha256"][:2]}/{row["sha256"]}'
            if row['object_path']!=relative:raise ValueError('Invalid object reference')
            original=(settings.private_root/relative).resolve()
            if not original.is_relative_to(settings.private_root.resolve()):raise ValueError('Object outside private root')
            content=original.read_bytes()
            if hashlib.sha256(content).hexdigest()!=row['sha256']:raise ValueError('Object hash mismatch')
            target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():target.write_bytes(content)
            files[relative]=row['sha256']
    engine.dispose()
    body=json.dumps(table_rows,ensure_ascii=False,separators=(',',':')).encode()
    (destination/'records.json').write_bytes(body)
    manifest={'format':1,'revision':revision,'records_sha256':hashlib.sha256(body).hexdigest(),'counts':{k:len(v) for k,v in table_rows.items()},'table_hashes':{k:digest(v) for k,v in table_rows.items()},'files':files,'excluded_authority':sorted(EXCLUDED),'credentials':'reset_required','external_work':'paused','team_archive':'not_archived'}
    manifest['source_checkpoint']=source_checkpoint
    (destination/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    return manifest


def read_bundle(bundle):
    bundle=Path(bundle).resolve();manifest=json.loads((bundle/'manifest.json').read_text('utf-8'))
    from alembic.script import ScriptDirectory
    from app.cli import migration_config
    if manifest.get('revision')!=ScriptDirectory.from_config(migration_config()).get_current_head():
        raise ValueError('Use the matching source revision to restore an older bundle before upgrading')
    body=(bundle/'records.json').read_bytes()
    if hashlib.sha256(body).hexdigest()!=manifest['records_sha256']:raise ValueError('Backup hash mismatch')
    rows=json.loads(body)
    if set(rows)!=set(Base.metadata.tables):raise ValueError('Backup schema tables differ')
    for name,values in rows.items():
        if digest(values)!=manifest['table_hashes'][name] or len(values)!=manifest['counts'][name]:raise ValueError('Backup table control mismatch')
    for relative,sha in manifest['files'].items():
        path=(bundle/relative).resolve()
        if not path.is_relative_to(bundle) or relative!=f'objects/{sha[:2]}/{sha}' or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('Backup attachment mismatch')
    return manifest,rows


def restore(settings,bundle):
    manifest,rows=read_bundle(bundle)
    engine=make_engine(settings.database_url)
    with engine.connect() as connection:
        schema=connection.execute(text('SELECT current_schema()')).scalar_one()
        if not schema.startswith(('restore_','migration_')):raise ValueError('Restore requires isolated owned schema')
        tables=set(inspect(connection).get_table_names())
        for name in tables-{'alembic_version'}:
            quoted=connection.dialect.identifier_preparer.quote(name)
            if connection.execute(text(f'SELECT EXISTS(SELECT 1 FROM {quoted})')).scalar():raise ValueError('Restore target nonempty')
    from app.cli import migration_config
    from alembic import command
    config=migration_config();config.attributes['database_url']=settings.database_url;command.upgrade(config,manifest['revision'])
    protect_private_root(settings.private_root)
    for relative,sha in manifest['files'].items():
        target=(settings.private_root/relative).resolve()
        if not target.is_relative_to(settings.private_root.resolve()):raise ValueError('Restore object outside root')
        target.parent.mkdir(parents=True,exist_ok=True)
        data=(Path(bundle)/relative).read_bytes()
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=sha:raise ValueError('Existing object conflicts')
        if not target.exists():target.write_bytes(data)
    with engine.begin() as connection:
        # All FK checks remain enabled. Temporarily defer constraints for the
        # retained cross-module cycles, then validate and restore their flags.
        constraints=connection.execute(text("SELECT c.conname,t.relname,c.condeferrable,c.condeferred FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE c.contype='f' AND n.nspname=current_schema()" )).all()
        quote=connection.dialect.identifier_preparer.quote
        for name,table,_,_ in constraints:connection.execute(text(f'ALTER TABLE {quote(table)} ALTER CONSTRAINT {quote(name)} DEFERRABLE INITIALLY DEFERRED'))
        for name,values in rows.items():
            table=Base.metadata.tables[name];converted=[]
            for row in values:
                current=dict(row)
                for col in table.columns:
                    v=current.get(col.name)
                    if v is not None:
                        if isinstance(col.type,DateTime):current[col.name]=datetime.fromisoformat(v)
                        elif isinstance(col.type,Date):current[col.name]=date.fromisoformat(v)
                        elif isinstance(col.type,Numeric):current[col.name]=Decimal(v)
                converted.append(current)
            if converted:connection.execute(table.insert(),converted)
        connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        for name,table,defer,initial in constraints:
            flags=('DEFERRABLE INITIALLY DEFERRED' if initial else 'DEFERRABLE INITIALLY IMMEDIATE') if defer else 'NOT DEFERRABLE'
            connection.execute(text(f'ALTER TABLE {quote(table)} ALTER CONSTRAINT {quote(name)} {flags}'))
        verified=True
        for name,table in Base.metadata.tables.items():
            actual=[json_value(dict(r)) for r in connection.execute(select(table).order_by(table.c.id)).mappings()]
            verified &= digest(actual)==manifest['table_hashes'][name]
        if not verified:raise ValueError('Restored control readback mismatch')
        # Runtime fencing is added only after exact source table/hash verification.
        # These two explicit operational rows are not source business records.
        from app.models import AppSetting,utcnow
        from uuid import uuid4
        now=utcnow()
        for key,value in [('migration.writer',{'state':'frozen','generation':None,'source':'verified isolated restore'}),
            ('migration.restore_gate',{'state':'needs_reconciliation','restore_verified':True,'manifest_hash':digest(manifest),'records_sha256':manifest['records_sha256'],'source_schema':(manifest.get('source_checkpoint') or {}).get('schema')})]:
            existing=connection.execute(select(AppSetting.id).where(AppSetting.key==key)).scalar()
            if existing:connection.execute(AppSetting.__table__.update().where(AppSetting.id==existing).values(value=value,updated_at=now))
            else:connection.execute(AppSetting.__table__.insert().values(id=str(uuid4()),version=1,created_at=now,updated_at=now,key=key,value=value))
    engine.dispose()
    return {'verified':True,'counts':manifest['counts'],'files':len(manifest['files']),'authority':'disabled','revision':manifest['revision'],'runtime_fence':'frozen','runtime_control_keys':['migration.writer','migration.restore_gate']}


def archive_team(bundle,adapter=None):
    if adapter is None:return {'state':'not_archived'}
    manifest,rows=read_bundle(bundle)
    content=json.dumps(rows,separators=(',',':')).encode()
    key=manifest['records_sha256']+'.json'
    adapter.write(key,content)
    if hashlib.sha256(adapter.read(key)).digest()!=hashlib.sha256(content).digest():raise ValueError('Team archive readback mismatch')
    return {'state':'archived','sha256':hashlib.sha256(content).hexdigest()}
