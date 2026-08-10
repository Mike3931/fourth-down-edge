"""Read endpoints must return their database connection.

`get_session()` hands back a new Session and closes nothing. Every read
endpoint called it bare, so each request checked a connection out of the
pool and never checked it back in. The default pool is 5 with 10 overflow,
so the fifteenth request exhausted it: further calls blocked for thirty
seconds and then failed with

    QueuePool limit of size 5 overflow 10 reached, connection timed out

A service that stops answering after fifteen page loads. It surfaced as
the Model Audit screen hanging on "Asking the engine…" after a day of
ordinary use, which is not a symptom anyone would trace to connection
handling — the reason it survived this long.

The write paths were always correct: `session_scope()` closes in a
`finally`. Only the older read endpoints were affected.

These assert the pool ledger directly rather than trying to provoke a
timeout. Counting checked-out connections is exact and fast; driving 15
requests and waiting 30 seconds for a hang is neither.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fde_api.api.main import app
from fde_api.db.engine import get_engine

# Every endpoint that used to call `get_session()` without closing it.
READ_ENDPOINTS = [
    "/health",
    "/v1/models",
    "/v1/performance/calibration",
    "/v1/performance/model-comparison",
]


@pytest.fixture()
def client(monkeypatch, tmp_path) -> TestClient:
    from fde_api.config import settings
    from fde_api.db.models import Base

    url = f"sqlite:///{(tmp_path / 'leak.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setenv("FDE_ALLOW_UNAUTHENTICATED", "1")

    import fde_api.db.engine as db_engine

    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]
    Base.metadata.create_all(get_engine())
    yield TestClient(app)
    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]


def _checked_out() -> int:
    """Connections handed out by the pool and not yet returned."""
    pool = get_engine().pool
    return pool.checkedout()  # type: ignore[attr-defined]


class TestConnectionsAreReturned:
    @pytest.mark.parametrize("endpoint", READ_ENDPOINTS)
    def test_a_single_request_leaves_nothing_checked_out(
        self, client: TestClient, endpoint: str
    ) -> None:
        client.get(endpoint)
        assert _checked_out() == 0, f"{endpoint} kept a connection"

    def test_many_requests_leave_nothing_checked_out(
        self, client: TestClient
    ) -> None:
        """Well past pool_size + max_overflow = 15, which is where the old
        behaviour stopped answering."""
        for _ in range(40):
            for endpoint in READ_ENDPOINTS:
                client.get(endpoint)
        assert _checked_out() == 0

    def test_the_service_still_answers_after_the_pool_would_have_been_exhausted(
        self, client: TestClient
    ) -> None:
        """The user-visible property: request 16 behaves like request 1."""
        for _ in range(30):
            client.get("/v1/performance/model-comparison")
        response = client.get("/v1/performance/model-comparison")
        assert response.status_code == 200

    def test_a_request_that_404s_also_returns_its_connection(
        self, client: TestClient
    ) -> None:
        """An early `raise HTTPException` must not skip the cleanup. A
        dependency's `finally` runs; a bare `get_session()` in the body
        would not have."""
        for _ in range(20):
            client.get("/v1/models/does-not-exist")
        assert _checked_out() == 0
