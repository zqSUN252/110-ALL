"""Real nonempty upgrade/downgrade guards, on disposable PG schemas only."""
import pytest
from alembic import command
from sqlalchemy import select,text
from datetime import date
from app.cli import migration_config
from app.models import LegacyPaymentObservation,LegacyBill,LegacyShipment,Ticket,Arrival,PackingDocument
from test_migration import applied
from test_finance import actor,seed
from app.migration.service import resolve_bill,resolve_shipment


def test_new_revisions_refuse_loss_of_source_time_unknown_kg_shared_bill_and_batch(app):
    applied(app)
    config=migration_config();config.attributes['database_url']=app.state.settings.database_url
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,party=seed(db,who)
        observation=db.scalar(select(LegacyPaymentObservation));observation.approval_time_basis='source_recorded_unverified'
        bill=db.scalar(select(LegacyBill));ship=db.scalar(select(LegacyShipment))
        resolve_bill(db,who,bill.id,{'uploaded_on':'2026-01-01','dates_source':'synthetic actual upload and original due-date evidence','expected_version':1,'warehouse_id':wh,'party_id':party,'source':'synthetic','idempotency_key':'bill'})
        resolve_shipment(db,who,ship.id,{'expected_version':1,'warehouse_id':wh,'uploaded_on':'2026-01-01','source':'synthetic','idempotency_key':'ship'})
        ticket=db.scalar(select(Ticket));arrival=Arrival(ticket_id=ticket.id,occurred_on=date(2026,1,1),pieces=1,kg=None,cbm='.5',actor_id=who.id,source='synthetic actual observation');db.add(arrival)
    with pytest.raises(RuntimeError,match='approval time'):command.downgrade(config,'f11000500003')
    with app.state.engine.begin() as c:c.execute(text('UPDATE legacy_payment_observations SET approval_time_basis=NULL'))
    with pytest.raises(RuntimeError,match='unknown observed KG'):command.downgrade(config,'f11000500002')
    # Synthetic disposable schema only: resolve the test observation before testing the next guard.
    with app.state.engine.begin() as c:c.execute(text('UPDATE cargo_arrivals SET kg=1'))
    with pytest.raises(RuntimeError,match='shared group'):command.downgrade(config,'f11000500001')
    with app.state.engine.begin() as c:c.execute(text('UPDATE tickets SET invoice_id=NULL'))
    with pytest.raises(RuntimeError,match='source|migration|historical',):command.downgrade(config,'e11000700002')
    command.upgrade(config,'head')
    command.check(config)
