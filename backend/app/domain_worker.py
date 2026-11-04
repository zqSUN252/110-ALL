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
    parser.add_argument('kind',choices=['recognition','delivery','media','translation','cargo-export','document-export','dialogue','channel-turn','assistant-file','connection','connection-monitor','channel-automation'])
    parser.add_argument('--operator-id')
    args=parser.parse_args(); settings=load_settings(); engine=make_engine(settings.database_url); factory=session_factory(engine)
    try:
        if args.kind in ('channel-turn','assistant-file','connection-monitor','channel-automation'):
            from app.dialogue.worker import channel_turn_once
            from app.dialogue.files import file_once
            from app.connections.worker import monitor_once
            from app.channels.automation import automation_once
            result={'channel-turn':channel_turn_once,'assistant-file':file_once,'connection-monitor':monitor_once,'channel-automation':automation_once}[args.kind](factory,settings)
            # Worker console contains only operational metadata, never bodies/nonces/domain results.
            print(json.dumps({k:v for k,v in result.items() if k in ('id','status','called','error_code')},ensure_ascii=False));return
        if args.kind=='document-export':
            from app.documents.exports import export_once
            print(json.dumps({'processed':export_once(settings,factory)},ensure_ascii=False));return
        if args.kind=='cargo-export':
            from app.warehouses.exports import export_once
            print(json.dumps(export_once(factory,settings),ensure_ascii=False));return
        if args.kind=='media' and not args.operator_id:parser.error('media requires an authorized --operator-id')
        if args.kind=='dialogue':
            from app.dialogue.worker import dialogue_once
            coroutine=dialogue_once(factory,settings)
        elif args.kind=='connection':
            from app.connections.worker import connection_once
            coroutine=connection_once(factory,settings)
        elif args.kind=='translation':
            from app.cargo.translation import translate_once
            coroutine=translate_once(factory,settings)
        else:coroutine=run_once(factory,settings) if args.kind=='recognition' else delivery_once(factory,settings) if args.kind=='delivery' else media_once(factory,settings,args.operator_id)
        print(json.dumps(asyncio.run(coroutine),ensure_ascii=False))
    finally:engine.dispose()


if __name__=='__main__':main()
