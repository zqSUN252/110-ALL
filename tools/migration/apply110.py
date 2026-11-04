"""Controlled CLI: private acquired export and explicitly reviewed native mappings."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from sqlalchemy import select
from app.settings import load_settings
from app.db import make_engine,session_factory
from app.models import User,Grant
from app.common import Actor
from app.migration.legacy110 import native_plan,apply_native
from app.private_storage import protect_private_root

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','apply']);p.add_argument('--export',required=True);p.add_argument('--mapping',required=True);p.add_argument('--dataset',required=True);p.add_argument('--plan',required=True);p.add_argument('--actor',required=True);a=p.parse_args()
    settings=load_settings();root=settings.private_root.resolve()
    for path in (a.export,a.mapping,a.plan):
        if not Path(path).resolve().is_relative_to(root):raise ValueError('Private controlled source paths required')
    source=json.loads(Path(a.export).read_text('utf-8'));mapping=json.loads(Path(a.mapping).read_text('utf-8'));plan=native_plan(source,a.dataset,mapping)
    if a.action=='plan':
        protect_private_root(Path(a.plan).parent);Path(a.plan).write_text(json.dumps(plan),encoding='utf-8');print(json.dumps({'state':'planned','plan_hash':plan['plan_hash']}));return
    if json.loads(Path(a.plan).read_text('utf-8'))!=plan:raise ValueError('Reviewed plan changed')
    engine=make_engine(settings.database_url)
    try:
        with session_factory(engine)() as db,db.begin():
            db.info['settings']=settings
            user=db.scalar(select(User).where(User.username==a.actor,User.active.is_(True)))
            if not user or not db.scalar(select(Grant.id).where(Grant.user_id==user.id,Grant.capability=='*')):raise ValueError('Current active administrator required')
            from app.migration.operations import writer_gate
            writer_gate(db);result=apply_native(db,Actor(user.id,{'*'},set()),source,plan)
        print(json.dumps(result))
    finally:engine.dispose()

if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'state':'failed','error_type':type(e).__name__}),file=sys.stderr);sys.exit(1)
