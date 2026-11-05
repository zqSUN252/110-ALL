"""Extend the owned 125k benchmark with non-zero native groups and measure group reads.

Synthetic benchmark only. Does not alter the supplied snapshot or production data.
Run after the 125k population exists and the benchmark schema is at current head.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from sqlalchemy import select, text
from app.settings import load_settings
from app.db import make_engine, session_factory
from app.models import Ticket, User
from app.common import Actor
from app.packing import service as packing
from app.documents.projection import packing_projection

TARGET = 125000
GROUP_SIZE = 5


def main():
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    with engine.begin() as db:
        schema = db.scalar(text('select current_schema()'))
        if not schema.startswith('test_bench_'):
            raise ValueError('Owned synthetic benchmark schema required')
        tickets = db.scalar(text("select count(*) from tickets where receipt like 'BENCH-%'"))
        if tickets != TARGET:
            raise ValueError('Expected the 125k synthetic population')
        if db.scalar(text('select count(*) from native_billing_groups')):
            raise ValueError('Native group population already present')
        actor_id = db.scalar(text("select id from users where username='admin' limit 1")) or db.scalar(text('select id from users order by id limit 1'))
        if not actor_id:
            raise ValueError('Benchmark user missing')
        started = time.perf_counter()
        db.execute(text('''
            WITH selected AS (
                SELECT * FROM tickets WHERE receipt LIKE 'BENCH-%' ORDER BY receipt LIMIT :limit
            ), numbered AS (
                SELECT *, ((row_number() OVER (ORDER BY receipt)) - 1) / :size AS g FROM selected
            )
            INSERT INTO native_billing_groups(id,version,created_at,updated_at,document_id,warehouse_id,party_id,invoice_id,state)
            SELECT md5('bench-ng-'||g)::uuid::text,1,now(),now(),min(document_id),min(warehouse_id),min(party_id),NULL,'active'
            FROM numbered GROUP BY g
        '''), {'limit': TARGET, 'size': GROUP_SIZE})
        db.execute(text('''
            WITH selected AS (
                SELECT * FROM tickets WHERE receipt LIKE 'BENCH-%' ORDER BY receipt LIMIT :limit
            ), numbered AS (
                SELECT *, ((row_number() OVER (ORDER BY receipt)) - 1) / :size AS g FROM selected
            )
            UPDATE tickets t SET billing_group_id=md5('bench-ng-'||n.g)::uuid::text
            FROM numbered n WHERE t.id=n.id
        '''), {'limit': TARGET, 'size': GROUP_SIZE})
        db.execute(text('''
            WITH seed AS (SELECT * FROM invoices ORDER BY id LIMIT 1)
            INSERT INTO invoices(id,version,created_at,updated_at,party_id,warehouse_id,scope,kind,reference,amount_ngn,rounding_adjustment_ngn,uploaded_on,due_on,period,source_key,pricing,state,charge_usd,charge_ngn,ngn_per_usd)
            SELECT md5('bench-ng-inv-'||g.id)::uuid::text,1,now(),now(),g.party_id,g.warehouse_id,seed.scope,'AR','BENCH-NG-'||g.id,5000,0,seed.uploaded_on,seed.due_on,NULL,'group:'||g.id,'{}'::jsonb,'open',NULL,NULL,NULL
            FROM native_billing_groups g CROSS JOIN seed
        '''))
        db.execute(text("UPDATE native_billing_groups g SET invoice_id=md5('bench-ng-inv-'||g.id)::uuid::text"))
        db.execute(text('UPDATE tickets t SET invoice_id=g.invoice_id FROM native_billing_groups g WHERE t.billing_group_id=g.id'))
        db.execute(text('''
            INSERT INTO cargo_arrivals(id,version,created_at,updated_at,ticket_id,occurred_on,pieces,kg,cbm,freight_usd,clearing_ngn,charge_basis,actor_id,source)
            SELECT md5('bench-ng-arr-'||t.id)::uuid::text,1,now(),now(),t.id,'2026-11-01',1,1,1,0,0,'{}'::jsonb,:actor,'Synthetic group scale arrival'
            FROM tickets t WHERE t.billing_group_id IS NOT NULL
        '''), {'actor': actor_id})
    with engine.begin() as db:
        db.execute(text('ANALYZE'))
    elapsed = time.perf_counter() - started

    with engine.connect() as db:
        counts = {
            'groups': db.scalar(text('select count(*) from native_billing_groups')),
            'members': db.scalar(text('select count(*) from tickets where billing_group_id is not null')),
            'groups_with_invoice': db.scalar(text('select count(*) from native_billing_groups where invoice_id is not null')),
            'arrived_members': db.scalar(text('select count(*) from cargo_arrivals a join tickets t on t.id=a.ticket_id where t.billing_group_id is not null')),
            'member_distribution': dict(db.execute(text('select member_count,count(*) from (select billing_group_id,count(*) member_count from tickets where billing_group_id is not null group by billing_group_id) x group by member_count')).all()),
        }
        document_id = db.scalar(text('select t.document_id from tickets t where t.billing_group_id is not null order by t.receipt limit 1'))
        plan = db.execute(text('''
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT g.id,count(t.id) FROM native_billing_groups g
            LEFT JOIN tickets t ON t.billing_group_id=g.id
            GROUP BY g.id ORDER BY g.id LIMIT 50
        ''')).scalar()[0]
    with factory() as db, db.begin():
        db.info['settings'] = settings
        actor = Actor(actor_id, {'*'}, set())
        for _ in range(3):
            packing.document_groups(db, actor, document_id)
        start = time.perf_counter()
        for _ in range(10):
            groups = packing.document_groups(db, actor, document_id)
        document_groups_seconds = (time.perf_counter() - start) / 10
        tickets = list(db.scalars(select(Ticket).where(Ticket.document_id == document_id)))
        start = time.perf_counter()
        for _ in range(10):
            packing_projection(db, tickets)
        projection_seconds = (time.perf_counter() - start) / 10
    result = {
        'status': 'passed',
        'scope': 'Owned synthetic 125k benchmark extension; not production/external acceptance',
        'note': 'The original 125k bank/list metrics were measured before this extension and must not be relabeled as group-path results.',
        'elapsed_seconds': elapsed,
        'counts': counts,
        'document_id_tested': document_id,
        'document_ticket_count': len(tickets),
        'document_group_count': len(groups),
        'timings_seconds': {
            'document_groups_per_call': document_groups_seconds,
            'packing_projection_per_50_tickets': projection_seconds,
        },
        'group_list_plan': {
            'execution_time_ms': plan['Execution Time'],
            'plan_rows': plan['Plan']['Plan Rows'],
            'shared_hit_blocks': plan['Plan'].get('Shared Hit Blocks'),
            'shared_read_blocks': plan['Plan'].get('Shared Read Blocks'),
        },
        'external_calls': 0,
    }
    output = Path(settings.private_root).parent / 'native-group-scale.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'counts', 'elapsed_seconds', 'timings_seconds', 'group_list_plan')}, ensure_ascii=False))
    engine.dispose()


if __name__ == '__main__':
    main()
