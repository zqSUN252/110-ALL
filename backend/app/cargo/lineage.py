"""Immutable stock transfer edges retain current manufacturer control sources."""
from sqlalchemy import select
from app.models import Receipt, StockEvent
from app.finance.service import record


def receipt_lineage(db, identity):
    # UNION (rather than UNION ALL) bounds an accidentally cyclic historical graph.
    sources=select(Receipt.id.label('id')).where(Receipt.id==identity).cte('receipt_sources',recursive=True)
    sources=sources.union(select(StockEvent.source_receipt_id).join(sources,StockEvent.receipt_id==sources.c.id)
                          .where(StockEvent.kind=='merge_in',StockEvent.source_receipt_id.is_not(None)))
    receipts=list(db.scalars(select(Receipt).where(Receipt.id.in_(select(sources.c.id))).order_by(Receipt.id)))
    events=list(db.scalars(select(StockEvent).where(StockEvent.kind=='merge_in',StockEvent.receipt_id.in_([r.id for r in receipts])).order_by(StockEvent.id)))
    return {'receipts':[record(r) for r in receipts],'transfers':[record(e) for e in events]}
