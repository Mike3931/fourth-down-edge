"""Engine/session factory. DATABASE_URL decides SQLite vs PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from fde_api.config import settings


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    url = url or settings.database_url
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):
        # SQLite needs FK enforcement opted in per-connection.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def get_session(url: str | None = None) -> Session:
    return sessionmaker(bind=get_engine(url), future=True)()


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    session = get_session(url)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
