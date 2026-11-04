"""Isolated browser acceptance server: only outbound phone transport is fake."""
import json,sys
from pathlib import Path
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import httpx
from sqlalchemy import text
from app.settings import load_settings
from app.main import create_app
def create():
    settings=load_settings()
    app=create_app(settings)
    with app.state.engine.connect() as db:
        if not db.scalar(text('select current_schema()')).startswith('test_ui_'):raise ValueError('Synthetic UI schema required')
    app.state.settings=replace(settings,sending_enabled=True,phone_delivery_enabled=True,phone_provider_url='https://synthetic.invalid/verify',phone_provider_token='synthetic-only')
    def delivery(request):
        if str(request.url)!='https://synthetic.invalid/verify':raise RuntimeError('Only synthetic phone transport allowed')
        body=json.loads(request.content)
        (settings.private_root/'phone-transport.json').write_text(json.dumps(body),encoding='utf-8')
        return httpx.Response(200,json={'delivery_reference':'synthetic-transport'})
    app.state.phone_transport=httpx.MockTransport(delivery)
    return app
