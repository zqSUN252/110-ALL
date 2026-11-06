"""Resolve the supplied MCSTAR snapshot into native records, with explicit source dates.

Only runs against the supplied-snapshot profile and its current schema. Records that
lack a party, shipment, date or complete relation are skipped and reported by code.
No business names, phones or amounts are printed.
"""
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, text
from app.settings import load_settings
from app.db import make_engine, session_factory
from app.common import Actor, DomainError
from app.models import (AppSetting, Invoice, LegacyBill, LegacyParty, LegacyShipment,
                        LegacyResolution, PackingDocument, Party, PartyWarehouse,
                        Shipment, Ticket, Warehouse, User)
from app.migration import service as migration

PROFILE = Path(__file__).resolve().parents[1] / 'private/supplied-snapshot-runtime.json'
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def date_text(value):
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value[:10] if DATE.match(text_value[:10]) else None


def epoch_date(value):
    if value in (None, ''):
        return None
    try:
        return datetime.fromtimestamp(int(str(value)[:13]) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def warehouse_name(item, index):
    text_value = str(item.get('provided_text') or '').strip()
    first = text_value.splitlines()[0] if text_value else ''
    for marker in ('Warehouse Address:', 'Warehouse Contact:'):
        if marker in first:
            return first.split(marker, 1)[0].strip() or 'Warehouse %d' % (index + 1)
    if 'Lijin Logistics Park' in text_value:
        return 'Lijin Logistics Park'
    if '义乌' in text_value:
        return '义乌仓'
    return first[:80] or 'Warehouse %d' % (index + 1)


def main():
    if Path(sys.argv[1] if len(sys.argv) > 1 else '').resolve() != PROFILE.resolve():
        raise SystemExit('Explicit supplied-snapshot profile required')
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    errors = Counter()
    skipped = Counter()
    report = {}

    def code(error):
        if isinstance(error, DomainError):
            return error.code
        return type(error).__name__

    def attempt(label, call):
        try:
            with db.begin_nested():
                call()
            return True
        except Exception as error:  # noqa: BLE001 - each record is isolated by a savepoint
            errors[label + ':' + code(error)] += 1
            return False

    with factory() as db, db.begin():
        db.info['settings'] = settings
        schema = db.execute(text('select current_schema()')).scalar_one()
        if not schema.startswith('supplied_snapshot'):
            raise SystemExit('Supplied snapshot schema required')
        profiles = db.scalar(select(AppSetting.value).where(AppSetting.key == 'migration.warehouse_profiles'))
        items = (profiles or {}).get('items') or []
        warehouses = []
        for index, item in enumerate(items):
            name = warehouse_name(item, index)
            existing = db.scalar(select(Warehouse).where(Warehouse.name == name))
            if existing is None:
                existing = Warehouse(name=name, address=str(item.get('provided_text') or '') or None,
                                     contact=None, source='supplied snapshot warehouse profile ' + str(item.get('source_pointer') or index))
                db.add(existing); db.flush()
            warehouses.append((item.get('kind'), existing))
        origin = next((warehouse for kind, warehouse in warehouses if kind == 'china_warehouse' and 'Lijin' in warehouse.name), None)
        origin = origin or next((warehouse for kind, warehouse in warehouses if kind == 'china_warehouse'), None)
        if origin is None:
            raise SystemExit('No China warehouse profile available')
        user = db.scalar(select(User).where(User.username == 'local-review'))
        actor = Actor(user.id, {'*'}, set()) if user else None
        if actor is None:
            raise SystemExit('local-review account required')
        report['warehouses_created'] = len(warehouses)
        report['origin_warehouse'] = origin.name

        parties = list(db.scalars(select(LegacyParty).where(LegacyParty.native_id.is_(None), LegacyParty.state != 'tombstoned').order_by(LegacyParty.id)))
        party_ok = 0
        for obj in parties:
            if attempt('party', lambda obj=obj: migration.resolve_party(
                    db, actor, obj.id,
                    {'warehouse_id': origin.id, 'expected_version': obj.version,
                     'source': 'supplied snapshot legacy party; warehouse assigned from source shipment origin Guangzhou',
                     'idempotency_key': 'backfill-party-' + obj.id})):
                party_ok += 1
        report['parties_total'] = len(parties)
        report['parties_resolved'] = party_ok

        bills = list(db.scalars(select(LegacyBill).where(LegacyBill.native_id.is_(None), LegacyBill.state != 'tombstoned').order_by(LegacyBill.id)))
        bill_ok = 0
        for obj in bills:
            if obj.relation_state == 'pending':
                skipped['bill_relation_pending'] += 1; continue
            party = db.get(LegacyParty, obj.party_id) if obj.party_id else None
            if party is None or not party.native_id:
                skipped['bill_party_missing'] += 1; continue
            uploaded = date_text(obj.raw.get('date'))
            due = date_text(obj.raw.get('dueDate'))
            if uploaded is None or due is None:
                skipped['bill_date_missing'] += 1; continue
            semantics = ('original_bill_date' if obj.kind == 'AP'
                         else 'source_bill_date_upload_semantics_unverified')
            if attempt('bill', lambda obj=obj, party=party, uploaded=uploaded, due=due, semantics=semantics:
                       migration.resolve_bill(
                           db, actor, obj.id,
                           {'warehouse_id': origin.id, 'party_id': party.native_id,
                            'expected_version': obj.version,
                            'uploaded_on': uploaded, 'due_on': due,
                            'dates_source': obj.pointer + '/date + /dueDate (source snapshot)',
                            'date_semantics': semantics,
                            'source': 'supplied snapshot legacy bill; source date fields retained with explicit semantics',
                            'idempotency_key': 'backfill-bill-' + obj.id})):
                bill_ok += 1
        report['bills_total'] = len(bills)
        report['bills_resolved'] = bill_ok

        shipments = list(db.scalars(select(LegacyShipment).where(LegacyShipment.native_id.is_(None), LegacyShipment.state != 'tombstoned').order_by(LegacyShipment.id)))
        ship_ok = 0
        for obj in shipments:
            uploaded = epoch_date(obj.raw.get('upAt'))
            semantics = 'source_up_at'
            if uploaded is None:
                uploaded = date_text(obj.raw.get('shipDate'))
                semantics = 'source_ship_date_upload_semantics_unverified'
            if uploaded is None:
                skipped['shipment_date_missing'] += 1; continue
            physical = obj.raw.get('bl') or None
            if attempt('shipment', lambda obj=obj, uploaded=uploaded, semantics=semantics, physical=physical:
                       migration.resolve_shipment(
                           db, actor, obj.id,
                           {'warehouse_id': origin.id, 'uploaded_on': uploaded,
                            'expected_version': obj.version,
                            'physical_reference': physical,
                            'dates_source': obj.pointer + '/upAt or /shipDate (source snapshot)',
                            'date_semantics': semantics,
                            'source': 'supplied snapshot legacy shipment; source date fields retained with explicit semantics',
                            'idempotency_key': 'backfill-shipment-' + obj.id})):
                ship_ok += 1
        report['shipments_total'] = len(shipments)
        report['shipments_resolved'] = ship_ok

        report['native'] = {
            'warehouses': db.scalar(select(func.count()).select_from(Warehouse)),
            'parties': db.scalar(select(func.count()).select_from(Party)),
            'party_warehouses': db.scalar(select(func.count()).select_from(PartyWarehouse)),
            'invoices': db.scalar(select(func.count()).select_from(Invoice)),
            'shipments': db.scalar(select(func.count()).select_from(Shipment)),
            'packing_documents': db.scalar(select(func.count()).select_from(PackingDocument)),
            'tickets': db.scalar(select(func.count()).select_from(Ticket)),
            'legacy_resolutions': db.scalar(select(func.count()).select_from(LegacyResolution)),
            'legacy_with_native': db.scalar(select(func.count()).select_from(LegacyParty).where(LegacyParty.native_id.is_not(None)))
                + db.scalar(select(func.count()).select_from(LegacyBill).where(LegacyBill.native_id.is_not(None)))
                + db.scalar(select(func.count()).select_from(LegacyShipment).where(LegacyShipment.native_id.is_not(None))),
        }
        report['errors'] = dict(errors)
        report['skipped'] = dict(skipped)
        output = Path(settings.private_root) / 'backfill-report.json'
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    engine.dispose()


if __name__ == '__main__':
    main()
