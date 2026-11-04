from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url, **kwargs):
    if not str(url).startswith('postgresql'):
        raise ValueError('PostgreSQL is required')
    return create_engine(url, pool_pre_ping=True, hide_parameters=True, **kwargs)


def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
