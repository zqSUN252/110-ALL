from functools import wraps
from app.common import DomainError,run_command,audit,json_value
from sqlalchemy import text


def domain_guard(db):db.execute(text('SELECT pg_advisory_xact_lock(110002)'))


def command(name,fields,authorize):
    """Short internal command transaction; caller owns commit, no network here."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(db,actor,p):
            unknown=set(p)-set(fields.split())-{'idempotency_key'}
            if unknown:raise DomainError('unknown_fields','Unsupported command fields',422,{'fields':sorted(unknown)})
            if name.startswith('assistant.'):domain_guard(db)
            authorize(db,actor,p)
            def apply():
                try:result=json_value(fn(db,actor,p))
                except KeyError:raise DomainError('missing_field','Required command field missing',422) from None
                db.flush()
                # Audit pointers and outcome only; source/model text stays in protected domain tables.
                audit(db,actor,name,'assistant',result.get('id','batch'),None,{k:v for k,v in result.items() if k in ('id','version','status','executed')})
                return result
            return run_command(db,actor,name,p.get('idempotency_key'),p,apply)
        return wrapped
    return decorate
