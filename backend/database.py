"""SQLAlchemy engine, session factory, declarative Base."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

_connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # Allow the session to be used across FastAPI's threadpool workers.
    _connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass
