"""Authoritative database-backend selection for tests.

Three backends, chosen explicitly rather than by accident:

  * SQLite functional        - fast local tests, no server needed
  * PostgreSQL integration   - schema and migration behaviour
  * PostgreSQL concurrency   - genuine multi-connection contention

The rule that matters: a test that REPORTS itself as PostgreSQL
concurrency must never silently run on SQLite. SQLite serialises writers
at the file level, so a race that "passes" there proves nothing about
PostgreSQL row locking — and reporting it as verified would be worse than
not running it at all.

So `pg_engine` raises when `FDE_DATABASE_URL` is absent or is not a
PostgreSQL URL. It does not fall back. The dedicated CI gate selects these
by marker, and a missing URL there fails the gate loudly.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

PG_ENV = "FDE_DATABASE_URL"


class PostgresRequired(RuntimeError):
    """A PostgreSQL-only test was asked to run without PostgreSQL."""


def _redact(url: str) -> str:
    """Strip credentials before anything prints the URL."""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _creds, host = rest.split("@", 1)
    return f"{scheme}://***:***@{host}"


def require_pg_url() -> str:
    url = os.environ.get(PG_ENV, "").strip()
    if not url:
        raise PostgresRequired(
            f"{PG_ENV} is not set. PostgreSQL concurrency tests must run against a "
            "real PostgreSQL server; falling back to SQLite would report a race as "
            "verified when SQLite had serialised it away."
        )
    if not url.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")):
        raise PostgresRequired(
            f"{PG_ENV} is not a PostgreSQL URL ({_redact(url)}); refusing to run "
            "PostgreSQL concurrency tests against another backend."
        )
    return url


@pytest.fixture(scope="session")
def pg_url() -> str:
    return require_pg_url()


@pytest.fixture(scope="session")
def pg_engine(pg_url: str) -> Engine:
    """A PostgreSQL engine with a real connection pool.

    `pool_size` is deliberately > 1: every race here needs at least two
    genuinely independent connections. A pool of one would serialise the
    workers and turn a race into a queue.
    """
    engine = create_engine(pg_url, future=True, pool_size=8, max_overflow=4, pool_pre_ping=True)
    with engine.connect() as c:
        c.execute(text("SELECT 1"))
    assert engine.dialect.name == "postgresql", engine.dialect.name
    return engine


def describe_backend(engine: Engine) -> dict[str, Any]:
    """Execution characteristics, captured and reported per §3.

    Printed by the concurrency suite so a passing run states on the record
    which server, isolation level and timeouts it actually exercised.
    """
    import sqlalchemy

    info: dict[str, Any] = {"dialect": engine.dialect.name}
    try:
        import psycopg

        info["psycopg"] = psycopg.__version__
    except Exception:  # pragma: no cover - reported, not required
        info["psycopg"] = "unknown"
    info["sqlalchemy"] = sqlalchemy.__version__
    with engine.connect() as c:
        info["server_version"] = c.execute(text("SHOW server_version")).scalar()
        info["database"] = c.execute(text("SELECT current_database()")).scalar()
        info["isolation_level"] = c.execute(text("SHOW transaction_isolation")).scalar()
        info["default_isolation"] = c.execute(
            text("SHOW default_transaction_isolation")
        ).scalar()
        info["lock_timeout"] = c.execute(text("SHOW lock_timeout")).scalar()
        info["statement_timeout"] = c.execute(text("SHOW statement_timeout")).scalar()
        info["max_connections"] = c.execute(text("SHOW max_connections")).scalar()
    info["url"] = _redact(str(engine.url))
    return info


def report_backend(engine: Engine, suite: str) -> dict[str, Any]:
    info = describe_backend(engine)
    print(f"\n=== {suite} :: BACKEND EVIDENCE ===")
    for k in (
        "dialect", "server_version", "database", "url", "isolation_level",
        "default_isolation", "lock_timeout", "statement_timeout",
        "max_connections", "sqlalchemy", "psycopg",
    ):
        print(f"    {k:22} {info.get(k)}")
    return info
