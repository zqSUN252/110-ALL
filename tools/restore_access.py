"""Local, explicitly selected native identity password reset after recovery.

Requires possession of private database configuration. Never activates imported
old users or the nonlogin importer; no fixed/default password and no HTTP bypass.
"""
import argparse,getpass,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from argon2 import PasswordHasher
from sqlalchemy import select,update
from app.settings import load_settings
from app.db import make_engine,session_factory
from app.models import User,Session,utcnow
from app.common import Actor,audit

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--username',required=True);parser.add_argument('--reason',required=True);args=parser.parse_args()
    password=os.environ.pop('MCSTAR_RECOVERY_PASSWORD',None) or getpass.getpass('New native account password: ')
    if len(password)<12 or not args.reason.strip():raise ValueError('Strong explicit password and recovery reason required')
    engine=make_engine(load_settings().database_url)
    try:
        with session_factory(engine)() as db,db.begin():
            user=db.scalar(select(User).where(User.username==args.username).with_for_update())
            if not user or not user.active or user.username.startswith('__migration_') or user.password_hash!='!reset-after-restore':raise ValueError('Only an active native identity disabled by verified restore may be reset')
            user.password_hash=PasswordHasher().hash(password);user.version+=1
            db.execute(update(Session).where(Session.user_id==user.id).values(revoked_at=utcnow()))
            audit(db,Actor(user.id,set(),set()),'recovery.password_reset','user',user.id,None,{'reason':args.reason,'local_operator':True})
        print('Restored native account reset; prior sessions remain invalid.')
    finally:engine.dispose()

if __name__=='__main__':
    try:main()
    except Exception:print('Recovery access reset failed; verify private configuration and selected restored native identity.',file=sys.stderr);sys.exit(1)
