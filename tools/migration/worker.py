"""Explicit local migration/backup worker, one job; no external transport."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from app.settings import load_settings
from app.db import make_engine,session_factory
from app.migration.worker import run_once
if __name__=='__main__':
    settings=load_settings();engine=make_engine(settings.database_url)
    try:print(json.dumps(run_once(session_factory(engine),settings)))
    finally:engine.dispose()
