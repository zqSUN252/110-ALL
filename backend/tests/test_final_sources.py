from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select,func
import pytest
from app.common import DomainError
from app.models import StockEvent,Ticket,Receipt,Arrival,ArrivalDispatch,PackingDocument,Payment
from app.finance import service as finance
from app.packing import service as packing
from app.cargo import service as cargo
from app.cargo import source_allocations as sources
from app.shipments import service as shipping
from test_logistics import who,call,seed,receipt_payload


def native_setup(db):
    actor=who(db);wh,party=seed(db,actor)
    p=receipt_payload(wh,party);p['specs']=[dict(length='1',width='1',height='1',unit='M',pieces=10,kg='10')]
    receipt=call(db,actor,cargo.create_receipt,**p)
    shipment=call(db,actor,shipping.create_shipment,warehouse_id=wh,reference='Synthetic dispatch',transport='AIR')
    document=call(db,actor,packing.create_document,shipment_id=shipment['id'],reference='Synthetic native source',uploaded_on='2026-11-01')
    return actor,wh,party,receipt,shipment,document


def native_ticket(db,actor,party,r,d,qty):
    return call(db,actor,packing.create_ticket,document_id=d['id'],party_id=party,native_receipt_id=r['id'],receipt=r['reference'],mark='Synthetic native group',pieces=qty,kg=str(qty),cbm=str(qty),destination_warehouse_id=d['warehouse_id'],freight_usd='0',clearing_ngn=str(qty*100),charge_components=[dict(component='freight',currency='USD',unit='KG',amount='0',source='Synthetic'),dict(component='clearing',currency='NGN',unit='CBM',amount=str(qty*100),source='Synthetic')])


def test_native_four_plus_six_exact_dispatch_arrival_and_pickup(app):
    from app.documents import release
    from app.tracking import service as tracking
    from app.attachments import put_file
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        actor,wh,party,r,s,d=native_setup(db);a=native_ticket(db,actor,party,r,d,4);b=native_ticket(db,actor,party,r,d,6)
        call(db,actor,cargo.dispatch,shipment_id=s['id'],occurred_on='2026-11-02',rows=[dict(receipt_id=r['id'],expected_version=r['version'],pieces=10,kg='10',cbm='10')])
        event=db.scalar(select(StockEvent).where(StockEvent.kind=='outbound'))
        tickets=[]
        for ticket in (a,b):
            qty=ticket['pieces']
            arrived=call(db,actor,packing.arrive,id=ticket['id'],expected_version=ticket['version'],pieces=qty,kg=str(qty),cbm=str(qty),mode='air',occurred_on='2026-11-03',source='Synthetic actual destination arrival',dispatch_allocations=[dict(stock_event_id=event.id,expected_version=event.version,pieces=qty,kg=str(qty),cbm=str(qty))])
            call(db,actor,tracking.set_node,target_type='ticket',target_id=ticket['id'],expected_version=arrived['version'],node='warehouse_arrived',mode='manual',destination_warehouse_id=wh,occurred_at='2026-11-04T10:00:00+08:00',source='Synthetic destination check')
            tickets.append(packing.get_ticket(db,actor,ticket['id']))
        assert sources.dispatch_options(db,actor,db.get(Ticket,a['id']))[0]['remaining']=={'pieces':'0','kg':'0','cbm':'0'}
        assert tickets[0]['invoice_id']==tickets[1]['invoice_id']
        order=call(db,actor,release.issue_order,members=[dict(ticket_id=t['id'],expected_version=t['version']) for t in tickets],mode='credit',promise_on='2026-12-01',guarantor='Synthetic guarantor')
        assert release.eligible_order(db,actor,order['id'])['eligible']
        from PIL import Image
        from io import BytesIO
        stream=BytesIO();Image.new('RGB',(8,8),'white').save(stream,format='PNG');photo=put_file(db,actor,stream.getvalue(),'synthetic-pickup.png','image/png','user',actor.id)
        call(db,actor,release.physical_release,order_id=order['id'],expected_version=order['version'],ticket_id=a['id'],pieces=4,kg='4',cbm='4',occurred_at='2026-11-05T10:00:00+08:00',photo_ids=[photo.id])
        assert release.physical_totals(db,a['id'])['pieces']==4
        assert db.scalar(select(func.sum(ArrivalDispatch.pieces)))==10
        assert db.get(Receipt,r['id']).pieces==10 and len(list(db.scalars(select(Arrival))))==2


def test_native_reservations_concurrent_create_and_edit_cannot_overdraw(app):
    with app.state.session_factory() as db,db.begin():actor,wh,party,r,s,d=native_setup(db)
    def compete(_):
        try:
            with app.state.session_factory() as db,db.begin():native_ticket(db,actor,party,r,d,6)
            return 'ok'
        except DomainError as e:return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(compete,[1,2]))
    assert sorted(result)==['native_source_quantity','ok']
    with app.state.session_factory() as db,db.begin():
        existing=db.scalar(select(Ticket));b=native_ticket(db,actor,party,r,d,4)
        with pytest.raises(DomainError,match='source'):
            call(db,actor,packing.update_ticket,id=b['id'],expected_version=b['version'],kg='5')


def test_unknown_old_void_requires_explicit_current_selection_not_guessing(app):
    from test_packing import make_document
    with app.state.session_factory() as db,db.begin():
        actor=who(db);wh,party=seed(db,actor);d,a=make_document(db,actor,wh,party)
        b=call(db,actor,packing.create_ticket,document_id=d['id'],party_id=party,receipt='Older excluded source',pieces=1,kg='1',cbm='1')
        a_obj=db.get(Ticket,a['id']);b_obj=db.get(Ticket,b['id']);a_obj.state='void';b_obj.state='void';document=db.get(PackingDocument,d['id']);document.state='void';document.void_members=None;db.flush()
        unknown=packing.recover_preview(db,actor,d['id']);assert unknown['requires_member_selection'] and unknown['original_members_unknown']
        pv=packing.recover_preview(db,actor,d['id'],[a['id']])
        call(db,actor,packing.recover_document,id=d['id'],expected_version=pv['version'],selected_members=[a['id']],preview=pv,reason='New explicit recovery of selected current member')
        assert a_obj.state=='active' and b_obj.state=='void' and document.void_members is None
