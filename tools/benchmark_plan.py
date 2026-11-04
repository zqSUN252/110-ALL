"""Synthetic query plans only, retained privately without business payloads."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.settings import load_settings
from app.db import make_engine
from sqlalchemy import text

settings=load_settings();engine=make_engine(settings.database_url)
with engine.connect() as db:
    if not db.scalar(text('select current_schema()')).startswith('test_bench_'):raise ValueError('Synthetic benchmark only')
    plan=db.scalar(text('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT * FROM tickets ORDER BY created_at DESC,id LIMIT 50'))
    root=settings.private_root.parent;path=root/'ticket-list-plan-before-index.json'
    if path.exists():path=root/'ticket-list-plan-after-index.json'
    path.write_text(json.dumps(plan,indent=2))
    print(json.dumps({'execution_ms':plan[0]['Execution Time'],'plan_path':str(path)}))
