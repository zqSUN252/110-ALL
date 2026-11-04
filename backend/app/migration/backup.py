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
from app.migration.bundle_stream import tables as stream_tables,ArrayHash,file_hash,canonical_hash

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
    counts={};table_hashes={};files={};records_hash=hashlib.sha256()
    with engine.connect() as connection,connection.begin():
        connection.execute(text('SET TRANSACTION READ ONLY'))
        revision=connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        from app.migration.operations import fact_snapshot
        writer=connection.execute(text("SELECT version,value FROM app_settings WHERE key='migration.writer'")).mappings().first()
        source_checkpoint={'schema':connection.execute(text('SELECT current_schema()')).scalar_one(),
            'cutoff':connection.execute(text('SELECT transaction_timestamp()')).scalar_one().isoformat(),
            'writer':{'version':writer['version'],'value':writer['value']} if writer else {'version':0,'value':None},'facts':fact_snapshot(connection)}
        with (destination/'records.json').open('wb') as output:
            def write(value):output.write(value);records_hash.update(value)
            write(b'{')
            for index,(name,table) in enumerate(sorted(Base.metadata.tables.items())):
                if index:write(b',')
                write(json.dumps(name).encode()+b':[');control=ArrayHash()
                rows=[] if name in EXCLUDED else connection.execute(select(table).order_by(table.c.id).execution_options(yield_per=1000)).mappings()
                for source_row in rows:
                    row=json_value(inert(name,dict(source_row)))
                    if control.count:write(b',')
                    write(json.dumps(row,ensure_ascii=False,separators=(',',':')).encode());control.add(row)
                    # File references are captured inside the same read snapshot.
                    if name=='attachments':
                        relative=f'objects/{row["sha256"][:2]}/{row["sha256"]}'
                        if row['object_path']!=relative:raise ValueError('Invalid object reference')
                        original=(settings.private_root/relative).resolve()
                        if not original.is_relative_to(settings.private_root.resolve()):raise ValueError('Object outside private root')
                        if file_hash(original)!=row['sha256']:raise ValueError('Object hash mismatch')
                        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
                        if not target.exists():
                            import shutil
                            shutil.copyfile(original,target)
                        files[relative]=row['sha256']
                write(b']');counts[name]=control.count;table_hashes[name]=control.hexdigest()
            write(b'}')
    engine.dispose()
    manifest={'format':1,'revision':revision,'records_sha256':records_hash.hexdigest(),'counts':counts,'table_hashes':table_hashes,'files':files,'excluded_authority':sorted(EXCLUDED),'credentials':'reset_required','external_work':'paused','team_archive':'not_archived'}
    manifest['source_checkpoint']=source_checkpoint
    with (destination/'manifest.json').open('w',encoding='utf-8') as output:json.dump(manifest,output)
    return manifest


def validate_bundle(bundle):
    bundle=Path(bundle).resolve();manifest=json.loads((bundle/'manifest.json').read_text('utf-8'))
    from alembic.script import ScriptDirectory
    from app.cli import migration_config
    if manifest.get('revision')!=ScriptDirectory.from_config(migration_config()).get_current_head():
        raise ValueError('Use the matching source revision to restore an older bundle before upgrading')
    if file_hash(bundle/'records.json')!=manifest['records_sha256']:raise ValueError('Backup hash mismatch')
    seen=set()
    for name,values in stream_tables(bundle/'records.json'):
        if name not in Base.metadata.tables:raise ValueError('Backup schema tables differ')
        seen.add(name);control=ArrayHash()
        for row in values:control.add(row)
        if control.hexdigest()!=manifest['table_hashes'][name] or control.count!=manifest['counts'][name]:raise ValueError('Backup table control mismatch')
    if seen!=set(Base.metadata.tables):raise ValueError('Backup schema tables differ')
    for relative,sha in manifest['files'].items():
        path=(bundle/relative).resolve()
        if not path.is_relative_to(bundle) or relative!=f'objects/{sha[:2]}/{sha}' or file_hash(path)!=sha:raise ValueError('Backup attachment mismatch')
    return manifest


def read_bundle(bundle):
    # Existing incremental/archive callers retain their materialized contract.
    # Restore uses the streaming path directly; format-1 bytes stay compatible.
    manifest=validate_bundle(bundle)
    return manifest,{name:list(rows) for name,rows in stream_tables(Path(bundle)/'records.json')}


def restore(settings,bundle):
    manifest=validate_bundle(bundle)
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
        if target.exists() and file_hash(target)!=sha:raise ValueError('Existing object conflicts')
        if not target.exists():
            import shutil
            shutil.copyfile(Path(bundle)/relative,target)
    with engine.begin() as connection:
        # All FK checks remain enabled. Temporarily defer constraints for the
        # retained cross-module cycles, then validate and restore their flags.
        constraints=connection.execute(text("SELECT c.conname,t.relname,c.condeferrable,c.condeferred FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE c.contype='f' AND n.nspname=current_schema()" )).all()
        quote=connection.dialect.identifier_preparer.quote
        for name,table,_,_ in constraints:connection.execute(text(f'ALTER TABLE {quote(table)} ALTER CONSTRAINT {quote(name)} DEFERRABLE INITIALLY DEFERRED'))
        for name,values in stream_tables(Path(bundle)/'records.json'):
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
                if len(converted)==1000:connection.execute(table.insert(),converted);converted=[]
            if converted:connection.execute(table.insert(),converted)
        connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        for name,table,defer,initial in constraints:
            flags=('DEFERRABLE INITIALLY DEFERRED' if initial else 'DEFERRABLE INITIALLY IMMEDIATE') if defer else 'NOT DEFERRABLE'
            connection.execute(text(f'ALTER TABLE {quote(table)} ALTER CONSTRAINT {quote(name)} {flags}'))
        verified=True
        for name,table in Base.metadata.tables.items():
            control=ArrayHash()
            for row in connection.execute(select(table).order_by(table.c.id).execution_options(yield_per=1000)).mappings():control.add(json_value(dict(row)))
            verified &= control.hexdigest()==manifest['table_hashes'][name] and control.count==manifest['counts'][name]
        if not verified:raise ValueError('Restored control readback mismatch')
        # Runtime fencing is added only after exact source table/hash verification.
        # These two explicit operational rows are not source business records.
        from app.models import AppSetting,utcnow
        from uuid import uuid4
        now=utcnow()
        for key,value in [('migration.writer',{'state':'frozen','generation':None,'source':'verified isolated restore'}),
            ('migration.restore_gate',{'state':'needs_reconciliation','restore_verified':True,'manifest_hash':canonical_hash(manifest),'records_sha256':manifest['records_sha256'],'source_schema':(manifest.get('source_checkpoint') or {}).get('schema')})]:
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
