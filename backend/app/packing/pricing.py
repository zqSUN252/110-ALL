"""Explicit ticket pricing authority; reviewed totals never become an inferred unit rate."""
from app.common import DomainError
from app.models import QuoteSnapshot
from app.pricing.service import build_quote

TOTAL_FIELDS=('freight_usd','clearing_ngn','charge_components')
DRIVERS=('destination','origin','goods_class','received_on','transport','pieces','kg','cbm')
QUANTITIES=('pieces','kg','cbm')


def requested(changes):
    return any(k in changes for k in (*TOTAL_FIELDS,'manual','repricing','fx','ngn_per_usd'))


def apply(db,t,changes,before,persist=True):
    authority=before['pricing_authority'];decision=changes.get('repricing')
    if decision not in (None,'retain_totals','quote'):raise DomainError('repricing_decision','Choose retain_totals or quote')
    direct=any(k in changes for k in TOTAL_FIELDS)
    explicit_quote='manual' in changes or decision=='quote'
    if (direct and explicit_quote) or (decision=='retain_totals' and 'manual' in changes):raise DomainError('pricing_conflict','Choose reviewed totals or a quote, not both')
    if direct:
        t.quote_id=None;t.pricing_authority='totals';return None
    if decision=='retain_totals':
        if not str(changes.get('reason','')).strip():raise DomainError('pricing_reason','Confirm why the reviewed totals stay unchanged')
        t.quote_id=None;t.pricing_authority='totals';return None
    quantities_changed=any(getattr(t,k)!=before[k] for k in QUANTITIES)
    if authority in ('totals','review') and not explicit_quote:
        if quantities_changed:raise DomainError('pricing_decision_required','Choose retain_totals or an explicit new quote for changed quantities',409)
        return None
    if not explicit_quote and not any(k in changes for k in DRIVERS):return None
    old=db.get(QuoteSnapshot,before['quote_id']) if before['quote_id'] and authority=='quote' else None
    manual=changes.get('manual') if explicit_quote else {'unit_price':str(old.unit_price),'unit':old.unit,'currency':old.currency,'source':'Retained explicit override'} if old and old.manual else None
    rate=old.ngn_per_usd if old and old.ngn_per_usd else t.ngn_per_usd
    fx=changes.get('fx') or ({'direction':'NGN_PER_USD','rate':str(rate)} if rate else None)
    quote=build_quote(db,t,manual,fx,persist=persist)
    if explicit_quote and quote is None:raise DomainError('quote_unavailable','No confirmed quote for the explicit selection; existing price is retained',409)
    t.quote_id=quote.id if quote else None;t.pricing_authority='quote' if quote else 'pending'
    t.freight_usd=quote.charge_usd if quote else None;t.clearing_ngn=quote.charge_ngn if quote else None;t.ngn_per_usd=quote.ngn_per_usd if quote else None
    return quote


def snapshot(t):
    return {k:getattr(t,k) for k in ('pricing_authority','quote_id',*QUANTITIES)}
