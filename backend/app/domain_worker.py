"""Explicit one-shot domain workers. All gates are evaluated at runtime."""
import argparse
import asyncio
import json
from app.settings import load_settings
from app.db import make_engine,session_factory
from app.evidence.worker import run_once
from app.channels.worker import delivery_once,media_once


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('kind',choices=['recognition','delivery','media','translation','cargo-export'])
    parser.add_argument('--operator-id')
    args=parser.parse_args(); settings=load_settings(); engine=make_engine(settings.database_url); factory=session_factory(engine)
    try:
        if args.kind=='cargo-export':
            from app.warehouses.exports import export_once
            print(json.dumps(export_once(factory,settings),ensure_ascii=False));return
        if args.kind=='media' and not args.operator_id:parser.error('media requires an authorized --operator-id')
        if args.kind=='translation':
            from app.cargo.translation import translate_once
            coroutine=translate_once(factory,settings)
        else:coroutine=run_once(factory,settings) if args.kind=='recognition' else delivery_once(factory,settings) if args.kind=='delivery' else media_once(factory,settings,args.operator_id)
        print(json.dumps(asyncio.run(coroutine),ensure_ascii=False))
    finally:engine.dispose()


if __name__=='__main__':main()
