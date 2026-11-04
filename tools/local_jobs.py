"""Process local file/backup jobs only; never collect, pair, send, or call a model."""
import argparse
import time
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.settings import load_settings
from app.db import make_engine,session_factory
from app.documents.exports import export_once as documents
from app.warehouses.exports import export_once as cargo
from app.dialogue.files import file_once
from app.migration.worker import run_once as migration


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args()
    settings=load_settings();engine=make_engine(settings.database_url);factory=session_factory(engine)
    try:
        while True:
            # Each domain owns its transaction, authorization and idempotency checks.
            for name,run in [('documents',lambda:documents(settings,factory)),('cargo',lambda:cargo(factory,settings)),('assistant-file',lambda:file_once(factory,settings)),('migration',lambda:migration(factory,settings))]:
                try:run()
                except Exception as exc:
                    # No exception payload, query, path, credential or customer text in ordinary logs.
                    print(name+': '+type(exc).__name__,flush=True)
            if args.once:break
            time.sleep(1)
    finally:engine.dispose()


if __name__=='__main__':main()
