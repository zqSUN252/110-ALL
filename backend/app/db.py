from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url, **kwargs):
    if not str(url).startswith('postgresql'):
        raise ValueError('PostgreSQL is required')
    kwargs.setdefault('pool_timeout', 3)
    connect_args = dict(kwargs.pop('connect_args', {}))
    connect_args.setdefault('connect_timeout', 3)
    return create_engine(url, pool_pre_ping=True, hide_parameters=True, connect_args=connect_args, **kwargs)


def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
