from datetime import timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from sqlalchemy import select
from app.common import require,DomainError
from app.models import Receipt,StockEvent,utcnow
from app.finance.service import day,record
from app.cargo.commands import visible


def totals(rows):
    return {'pieces':sum(r['pieces'] for r in rows),'kg':f"{sum((Decimal(r['kg']) for r in rows),Decimal(0)):.6f}",'cbm':f"{sum((Decimal(r['cbm']) for r in rows),Decimal(0)):.6f}"}


def inbound(db,actor,on=None,warehouse_id=None,salesperson=None):
    require(actor,'cargo.read')
    settings=db.info.get('settings');zone=settings.business_timezone if settings else 'Asia/Shanghai'
    selected=day(on) if on else utcnow().astimezone(ZoneInfo(zone)).date()-timedelta(days=1)
    q=visible(select(StockEvent,Receipt).join(Receipt,Receipt.id==StockEvent.receipt_id).where(StockEvent.kind=='inbound',StockEvent.occurred_on==selected),StockEvent,actor)
    if warehouse_id:require(actor,'cargo.read',warehouse_id);q=q.where(StockEvent.warehouse_id==warehouse_id)
    rows=[]
    for e,r in db.execute(q.order_by(Receipt.reference,StockEvent.id)):
        if salesperson and (r.salesperson!=salesperson or r.salesperson.upper().startswith('MC')):continue
        rows.append({'receipt_id':r.id,'reference':r.reference,'warehouse_id':r.warehouse_id,'destination':r.destination,'salesperson':r.salesperson,'operator_name':r.operator_name,'pieces':e.pieces,'kg':f'{e.kg:.6f}','cbm':f'{e.cbm:.6f}'})
    total=totals(rows);destinations=[];salespeople=[]
    for dest in sorted({r['destination'] or '' for r in rows}):
        group=[r for r in rows if (r['destination'] or '')==dest];t=totals(group)
        destinations.append({'destination':dest or None,**t,'cbm_share':str(Decimal(t['cbm'])/Decimal(total['cbm'])) if Decimal(total['cbm']) else None})
    for name in sorted({r['salesperson'] for r in rows if r['salesperson'] and not r['salesperson'].upper().startswith('MC')}):
        salespeople.append({'name':name,**totals([r for r in rows if r['salesperson']==name]),'receipt_ids':[r['receipt_id'] for r in rows if r['salesperson']==name]})
    return {'on':selected.isoformat(),'totals':total,'destinations':destinations,'salespeople':salespeople,'rows':rows,'source':'native_inbound_ledger','external_f6_is_stock':False}
