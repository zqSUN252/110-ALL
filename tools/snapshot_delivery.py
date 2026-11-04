"""Renew the controlled supplied-snapshot backup and isolated restore evidence."""
import json,os,sys,time
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.settings import load_settings
from app.private_storage import protect_private_root
from app.migration.backup import backup,restore
from app.db import make_engine
from sqlalchemy import text
from sqlalchemy.engine import make_url
from dataclasses import replace

def main():
    profile=ROOT/'private/supplied-snapshot-runtime.json'
    if Path(os.environ.get('MCSTAR_CONFIG','')).resolve()!=profile.resolve():raise ValueError('Explicit supplied-snapshot profile required')
    settings=load_settings();identity=uuid4().hex;root=ROOT/'private/snapshot-delivery'/identity;protect_private_root(root)
    engine=make_engine(settings.database_url)
    with engine.connect() as db:
        head=db.scalar(text('select version_num from alembic_version'))
        if head!='g11000600002':raise ValueError('Expected final schema')
        counts={name:db.scalar(text('select count(*) from '+name)) for name in ['legacy_tickets','legacy_bills','legacy_shipments','legacy_parties']}
    start=time.perf_counter();manifest=backup(settings,root/'backup');backup_seconds=time.perf_counter()-start
    schema='restore_delivery_'+identity
    with engine.begin() as db:db.execute(text('CREATE SCHEMA '+schema))
    url=make_url(settings.database_url).update_query_dict({'options':'-csearch_path='+schema})
    target=replace(settings,database_url=url.render_as_string(hide_password=False),private_root=root/'restored-private')
    start=time.perf_counter();result=restore(target,root/'backup');restore_seconds=time.perf_counter()-start
    receipt={'schema_head':head,'counts':counts,'backup_seconds':backup_seconds,'restore_seconds':restore_seconds,'restore_schema':schema,'backup_path':str(root/'backup'),'records_sha256':manifest['records_sha256'],'verification':result,'external_calls':0}
    (root/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8');(ROOT/'private/snapshot-delivery/latest.txt').write_text(str(root),encoding='utf-8');engine.dispose()
    print(json.dumps({k:v for k,v in receipt.items() if k not in ['backup_path','verification']}|{'verified':result['verified'],'runtime_fence':result['runtime_fence']}))
if __name__=='__main__':main()
