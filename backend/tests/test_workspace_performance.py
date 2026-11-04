"""Query-bound regression for complete same-amount bank candidate populations."""
from sqlalchemy import event
from app.models import EvidenceCase
from app.bank import service as bank
from test_finance import actor,seed
from test_evidence import import_bank,case


def test_fifty_legitimate_equal_amount_candidates_keep_all_sources_with_bounded_queries(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);warehouse,_=seed(db,who)
        account,entries=import_bank(db,who,warehouse,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC'+str(n).zfill(3)] for n in range(50)])
        evidence=case(db,who,warehouse,account);current=db.get(EvidenceCase,evidence['id']);db.flush()
        queries=[]
        def observe(conn,cursor,statement,parameters,context,many):
            if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
        event.listen(app.state.engine,'before_cursor_execute',observe)
        try:result=bank.decision(db,current)
        finally:event.remove(app.state.engine,'before_cursor_execute',observe)
        assert len(result['candidates'])==50
        assert {c['bank']['id'] for c in result['candidates']}=={e.id for e in entries}
        assert all(c['bank']['file_sha256'] and c['bank']['sheet'] for c in result['candidates'])
        assert len(queries)<=12, f'50 candidates required {len(queries)} SELECTs'


def test_fifty_ticket_page_preserves_detail_projection_with_bounded_queries(app):
    from app.models import Ticket,CargoChargeComponent
    from app.packing import service as packing
    from test_logistics import who,seed as cargo_seed
    from test_packing import make_document
    with app.state.session_factory() as db,db.begin():
        current=who(db);wh,party=cargo_seed(db,current)
        document,first=make_document(db,current,wh,party)
        original=db.get(Ticket,first['id'])
        for n in range(49):
            ticket=Ticket(document_id=document['id'],warehouse_id=wh,party_id=party,receipt='SYN-PAGE-'+str(n),position=n+1,transport='AIR',origin='GZ',pieces=3,kg='3',cbm='1',freight_usd='1',clearing_ngn='0',ngn_per_usd='1500')
            db.add(ticket);db.flush()
            db.add(CargoChargeComponent(ticket_id=ticket.id,ticket_version=1,position=0,component='freight',currency='USD',unit='KG',amount='1',source='Synthetic reviewed basis'))
        db.flush()
        expected=packing.ticket_json(db,original)
        queries=[]
        def observe(conn,cursor,statement,parameters,context,many):
            if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
        event.listen(app.state.engine,'before_cursor_execute',observe)
        try:result=packing.list_tickets(db,current)
        finally:event.remove(app.state.engine,'before_cursor_execute',observe)
        assert len(result)==50
        assert next(r for r in result if r['id']==original.id)==expected
        assert all(r['charge_components'] for r in result)
        assert len(queries)<=12, f'50 ticket page required {len(queries)} SELECTs'
