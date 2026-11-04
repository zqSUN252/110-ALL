"""Create an isolated synthetic browser test runtime. Never alters the delivery dataset."""
import json
import sys
from pathlib import Path
from uuid import uuid4
from dataclasses import replace, asdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from sqlalchemy import text
from sqlalchemy.engine import make_url
from alembic import command
from argon2 import PasswordHasher
from app.settings import load_settings,protect_private_root
from app.db import make_engine,session_factory
from app.cli import migration_config
from app.models import User,Grant


def main():
    root=Path(__file__).resolve().parents[1]
    settings=load_settings()
    schema='test_ui_'+uuid4().hex
    engine=make_engine(settings.database_url)
    with engine.begin() as db:db.execute(text('CREATE SCHEMA '+schema))
    private=root/'private'/'ui-validation'/schema
    protect_private_root(private)
    scoped=replace(settings,database_url=make_url(settings.database_url).update_query_dict({'options':'-csearch_path='+schema}).render_as_string(hide_password=False),private_root=private,secure_cookies=False,collection_enabled=False,summaries_enabled=False,model_calls_enabled=False,sending_enabled=False,connection_control_enabled=False,connection_diagnostics_enabled=False,phone_delivery_enabled=False,main_account_read_only=True)
    config=migration_config();config.attributes['database_url']=scoped.database_url;command.upgrade(config,'head')
    scoped_engine=make_engine(scoped.database_url)
    with session_factory(scoped_engine)() as db,db.begin():
        for name,caps in [('admin',['*']),('restricted',[])]:
            user=User(username=name,password_hash=PasswordHasher().hash('synthetic-password-123'));db.add(user);db.flush()
            for capability in caps:db.add(Grant(user_id=user.id,capability=capability))
    values=asdict(scoped);values['private_root']=str(private)
    profile=private/'runtime.json';profile.write_text(json.dumps(values),encoding='utf-8')
    (root/'private'/'ui-validation'/'latest-profile.txt').write_text(str(profile),encoding='utf-8')
    print(json.dumps({'schema':schema,'profile_path':str(profile),'external_actions_enabled':False}))


if __name__=='__main__':main()
