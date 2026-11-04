"""Controlled local CLI. Normal stdout contains counts, hashes and status only."""
import argparse
from pathlib import Path
import sys
import json
import hashlib
from zipfile import ZipFile
from dataclasses import replace
from sqlalchemy import text,select
from sqlalchemy.engine import make_url
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app.migration.source import parse_source
from app.migration.service import plan_source,apply_source,verify_source,importer,upgrade_mapping
from app.common import Actor,json_value
from app.settings import load_settings
from app.db import make_engine,session_factory
from app.private_storage import protect_private_root
from app.models import MigrationBatch


def read_archive(path):
    with ZipFile(path) as archive:
        body=archive.read('mcstar_pkg/data/data.json')
        from app.migration.configuration import extract_profiles
        profiles=extract_profiles(archive.read('mcstar_pkg/prompts/用户提示词全记录.md'))
    return body,hashlib.sha256(Path(path).read_bytes()).hexdigest(),profiles


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['inventory','stage','reconcile','plan','apply','verify','mapping-upgrade'])
    parser.add_argument('--archive',required=True)
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--schema',default='supplied_snapshot')
    parser.add_argument('--plan')
    parser.add_argument('--expected-plan-hash')
    parser.add_argument('--source')
    args=parser.parse_args()
    if not __import__('re').fullmatch(r'(supplied|migration)_[a-z0-9_]+',args.schema):raise ValueError('Owned isolated schema required')
    settings=load_settings();output=settings.private_root/'migration'/args.schema;protect_private_root(output)
    body,archive_sha,profiles=read_archive(args.archive);parsed=parse_source(body)
    parsed['configuration']=profiles
    parsed['controls']['provided_warehouse_profiles']=len(profiles['warehouse_profiles'])
    parsed['controls']['public_contacts_present']=set(profiles['public'])=={'public_hotline','public_openshipment'}
    plan=plan_source(parsed,args.dataset,'supplied_snapshot')
    if args.action in ('inventory','stage','reconcile','plan'):
        if args.action=='stage':
            stage={k:v for k,v in parsed.items() if k!='files'}
            stage['files']=[{k:v for k,v in f.items() if k!='bytes'} for f in parsed['files']]
            (output/'stage.json').write_text(json.dumps(json_value(stage),ensure_ascii=False),encoding='utf-8')
        if args.action=='reconcile':
            (output/'reconcile.json').write_text(json.dumps(json_value({'issues':parsed['issues'],'bills':parsed['entities']['bills']}),ensure_ascii=False),encoding='utf-8')
        (output/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
        print(json.dumps({'action':args.action,'controls':parsed['controls'],'sha256':parsed['sha256'],'plan_hash':plan['plan_hash']}));return
    if not args.plan or json.loads(Path(args.plan).read_text('utf-8'))!=plan:raise ValueError('Reviewed matching plan required')
    master=make_engine(settings.database_url)
    with master.begin() as conn:conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS {args.schema}'))
    url=make_url(settings.database_url).update_query_dict({'options':f'-csearch_path={args.schema}'})
    profile=replace(settings,database_url=url.render_as_string(hide_password=False))
    from app.cli import migration_config
    from alembic import command
    config=migration_config();config.attributes['database_url']=profile.database_url;command.upgrade(config,'head')
    engine=make_engine(profile.database_url)
    with session_factory(engine)() as db,db.begin():
        db.info['settings']=profile
        if args.action=='mapping-upgrade':
            system=importer(db);result=upgrade_mapping(db,Actor(system.id,{'*'},set()),parsed,plan,args.expected_plan_hash,args.source)
        elif args.action=='apply':
            system=importer(db);actor=Actor(system.id,{'*'},set())
            result=apply_source(db,actor,parsed,plan,{'archive_sha256':archive_sha,'archive_path':str(Path(args.archive).resolve()),'member':'mcstar_pkg/data/data.json'})
        else:
            batch=db.scalar(select(MigrationBatch).where(MigrationBatch.dataset==args.dataset,MigrationBatch.sha256==parsed['sha256']))
            result=verify_source(db,parsed,batch.id if batch else None)
    (output/(args.action+'-result.json')).write_text(json.dumps(result),encoding='utf-8')
    if args.action=='verify' and result['state']=='verified':
        from dataclasses import asdict
        values=asdict(profile);values['private_root']=str(profile.private_root)
        (settings.private_root/'supplied-snapshot-runtime.json').write_text(json.dumps(values),encoding='utf-8')
    print(json.dumps(result));engine.dispose();master.dispose()


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'state':'failed','error_type':type(exc).__name__}),file=sys.stderr);sys.exit(1)
