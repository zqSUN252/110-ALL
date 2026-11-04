"""Independent Decimal/control reread, separate from the migration parser."""
from pathlib import Path
import argparse,json,sys,hashlib,base64
from decimal import Decimal
from zipfile import ZipFile
from sqlalchemy import select,func
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from app.settings import load_settings
from app.db import make_engine,session_factory
from app.models import LegacyBill,LegacyTicket,LegacyShipment,LegacyParty,LegacyF6,LegacyPaymentObservation,MigrationFile,MigrationBatch,Attachment,Payment,Session
from app.attachments import read_file


def verify(archive_path,settings):
    with ZipFile(archive_path) as z:body=z.read('mcstar_pkg/data/data.json')
    root=json.loads(body,parse_float=Decimal)
    data={k:(json.loads(v,parse_float=Decimal) if isinstance(v,str) and v.lstrip().startswith(('[','{')) else v) for k,v in root['data'].items()}
    source_bills={str(b['id']):b for b in data['logistics_bills']}
    source_ships={str(s['id']):s for s in data['logistics_shipments']}
    source_tickets={f'{s["id"]}:{i}':t for s in source_ships.values() for i,t in enumerate(s['tickets'])}
    def original_at(pointer):
        value=root
        for part in pointer.strip('/').split('/'):
            while isinstance(value,str) and value.lstrip().startswith(('[','{')):value=json.loads(value,parse_float=Decimal)
            part=part.replace('~1','/').replace('~0','~')
            value=value[int(part)] if isinstance(value,list) else value[part]
        return value
    counts={'parties':len(data['logistics_partners']),'shipments':len(source_ships),'tickets':len(source_tickets),'bills':len(source_bills),'slips':sum(len(b.get('slips',[])) for b in source_bills.values()),'ledger':len(data['logistics_payLedger']),'f6':len(data['logistics_f6recs'])}
    old_token_hashes={hashlib.sha256(str(token).encode()).hexdigest() for token in (root.get('tokens') or {})}
    engine=make_engine(settings.database_url)
    with session_factory(engine)() as db:
        bills=list(db.scalars(select(LegacyBill)));tickets=list(db.scalars(select(LegacyTicket)));ships=list(db.scalars(select(LegacyShipment)))
        count_ok=len(bills)==counts['bills'] and len(tickets)==counts['tickets'] and len(ships)==counts['shipments']
        for name,cls in (('parties',LegacyParty),('f6',LegacyF6)):
            count_ok &= db.scalar(select(func.count()).select_from(cls))==counts[name]
        for name,kind in (('slips','slip'),('ledger','ledger')):
            count_ok &= db.scalar(select(func.count()).select_from(LegacyPaymentObservation).where(LegacyPaymentObservation.kind==kind))==counts[name]
        money_ok=all(all(getattr(b,column)==Decimal(str(source_bills[b.source_key].get(field,0))) for column,field in (('amount','amount'),('received','received'),('discount','discount'))) and b.currency==source_bills[b.source_key]['cur'] and b.source_status==source_bills[b.source_key]['status'] for b in bills)
        quantity_ok=all(t.receipt==source_tickets[t.source_key]['receipt'] and t.pieces==source_tickets[t.source_key].get('pieces') and all(getattr(t,col)==(Decimal(str(source_tickets[t.source_key][field])) if source_tickets[t.source_key].get(field) is not None else None) for col,field in (('kg','weightKg'),('cbm','cbm'),('freight_usd','freightUSD'),('clearing_ngn','clearingNGN'))) for t in tickets)
        references_ok=all(s.source_no==source_ships[s.source_key]['no'] and s.source_bl==source_ships[s.source_key]['bl'] and s.physical_reference is None for s in ships)
        f6=list(db.scalars(select(LegacyF6)));copies_ok=sum(r.copied_at is not None for r in f6)==sum(bool(r.get('sent',{}).get('at')) for r in data['logistics_f6recs']) and all(r.state=='committed_external' for r in f6)
        files=list(db.scalars(select(MigrationFile)));objects=[db.get(Attachment,f.attachment_id) for f in files]
        file_ok=True
        for reference,attachment in zip(files,objects):
            original=original_at(reference.pointer);uri=original['data'] if isinstance(original,dict) else original
            original_hash=hashlib.sha256(base64.b64decode(uri.split(',',1)[1],validate=True)).hexdigest()
            file_ok &= original_hash==attachment.sha256==hashlib.sha256(read_file(settings,attachment)).hexdigest()
        source_sessions=db.scalar(select(func.count()).select_from(Session).where(Session.token_hash.in_(old_token_hashes))) if old_token_hashes else 0
        result={'source_sha256':hashlib.sha256(body).hexdigest(),'counts':counts,'counts_match':bool(count_ok),'money_status_match':money_ok,'quantities_match':quantity_ok,'source_references_preserved':references_ok,'f6_committed_copy_provenance_match':copies_ok,'attachment_references':len(files),'unique_attachment_objects':len({a.sha256 for a in objects}),'files_match':file_ok,'cash_created':db.scalar(select(func.count()).select_from(Payment)),'source_sessions_activated':source_sessions,'new_native_sessions':db.scalar(select(func.count()).select_from(Session))-source_sessions}
    engine.dispose()
    result['archive_sha256']=hashlib.sha256(Path(archive_path).read_bytes()).hexdigest()
    result['verified']=all(result[k] for k in ('counts_match','money_status_match','quantities_match','source_references_preserved','f6_committed_copy_provenance_match','files_match')) and result['cash_created']==0 and result['source_sessions_activated']==0
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--archive',required=True);p.add_argument('--report',required=True);a=p.parse_args()
    result=verify(a.archive,load_settings());Path(a.report).write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
