"""Unknown source dimensions remain unknown through arrival and pickup."""
from decimal import Decimal
from app.common import DomainError


def known_sum(values):
    values=list(values)
    return None if any(v is None for v in values) else sum(values,Decimal(0))


def nonkg_sea(db,t):
    from app.packing.allocation import components
    lines=components(db,t)
    return t.transport=='SEA' and t.kg is None and bool(lines) and all(c['unit']!='KG' and c['amount'] is not None for c in lines)


def complete(t,values):
    return all(values[k] is not None and values[k]==getattr(t,k) for k in ('pieces','kg','cbm') if getattr(t,k) is not None)


def remaining(total,used):
    return None if total is None or used is None else total-used
