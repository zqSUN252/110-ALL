"""Private backup/restore CLI. Never prints rows, credentials or source contents."""
import argparse
from pathlib import Path
import sys
import json
from dataclasses import replace
from sqlalchemy.engine import make_url
from sqlalchemy import text,select
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.settings import load_settings
from app.migration.backup import backup,restore,archive_team
from app.migration.operations import incremental,materialize,DirectoryArchive,cutover,backout_plan
from app.migration.service import importer
from app.db import make_engine,session_factory
from app.common import Actor
from datetime import datetime


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['backup','restore','incremental','materialize','archive','cutover','freeze','backout-plan'])
    p.add_argument('--path');p.add_argument('--base');p.add_argument('--current');p.add_argument('--schema');p.add_argument('--team-directory');p.add_argument('--generation');p.add_argument('--source');p.add_argument('--expected-version',type=int,default=0);p.add_argument('--after')
    a=p.parse_args();settings=load_settings()
    if a.action=='backup':
        m=backup(settings,a.path);result={k:m[k] for k in ('revision','counts','records_sha256','team_archive')}
    elif a.action=='restore':
        if not a.schema or not __import__('re').fullmatch('restore_[a-z0-9_]+',a.schema):raise ValueError('Owned restore schema required')
        engine=make_engine(settings.database_url)
        with engine.begin() as db:db.execute(text(f'CREATE SCHEMA IF NOT EXISTS {a.schema}'))
        engine.dispose();url=make_url(settings.database_url).update_query_dict({'options':f'-csearch_path={a.schema}'})
        target=replace(settings,database_url=url.render_as_string(hide_password=False),private_root=settings.private_root/'restores'/a.schema)
        result=restore(target,a.path)
    elif a.action=='incremental':result=incremental(a.base,a.current,a.path)
    elif a.action=='materialize':result=materialize(a.base,a.current,a.path)
    elif a.action=='archive':result=archive_team(a.path,DirectoryArchive(a.team_directory) if a.team_directory else None)
    else:
        engine=make_engine(settings.database_url)
        with session_factory(engine)() as db,db.begin():
            db.info['settings']=settings
            if a.action=='backout-plan':result=backout_plan(db,datetime.fromisoformat(a.after))
            else:
                technical=importer(db);result=cutover(db,Actor(technical.id,{'*'},set()),a.expected_version,a.generation,a.source,a.action=='cutover')
        engine.dispose()
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except Exception as error:print(json.dumps({'state':'failed','error_type':type(error).__name__}),file=sys.stderr);sys.exit(1)
