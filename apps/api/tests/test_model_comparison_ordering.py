"""Which evaluation the Model Audit screen shows when there is more than one.

`team-ratings-v1` carries two evaluations for each test scope: the before
and after of the deterministic-ordering correction. Both are kept, because
the evaluation record is append-only and the earlier run genuinely
happened. `reports/integrity/CERTIFICATION.md` records the earlier numbers
as SUPERSEDED and states plainly that they must not be cited.

The endpoint ordered by (scope, model_version_id) with no tiebreak, so the
pair came back in whatever order the database chose, and the screen kept
the first row it saw. It was displaying 0.21589 — the superseded value —
and would have displayed either one depending on the query plan.

This does not make the endpoint the authority on which run supersedes
which; that is settled in the certification record. It makes the answer
STABLE, and oldest-first so the screen can take the latest and say so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from fde_api.db.models import Base, ModelEvaluation, ModelVersion

SCOPE = "test:2024:PREGAME"
MODEL = "team-ratings-v1"
EARLIER = datetime(2026, 8, 1, 13, 31, 8, tzinfo=UTC)
LATER = EARLIER + timedelta(days=2)


@pytest.fixture()
def session(monkeypatch, tmp_path) -> Session:
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    import fde_api.db.engine as db_engine
    from fde_api.config import settings

    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setenv("FDE_ALLOW_UNAUTHENTICATED", "1")
    # The app's engine is lru_cached, so without this it would answer from
    # whichever database a previous test happened to leave cached.
    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()

    s.add(ModelVersion(
        id=MODEL, name=MODEL, algorithm="ratings", approval_status="research_only",
        target="margin", train_window="x", validation_window="y", test_window="z",
        dataset_hashes={}, metrics={}, known_limitations="", random_seed=1,
        hyperparameters={}, code_commit="abc1234", dependency_lock_hash="def5678",
        created_at=EARLIER,
    ))
    # Inserted LATEST-first on purpose: if the endpoint relied on insertion
    # order it would hand back the superseded row first, which is exactly
    # the failure being pinned.
    for created, brier in ((LATER, 0.21590024), (EARLIER, 0.21589059)):
        s.add(ModelEvaluation(
            id=f"{MODEL}:{SCOPE}:{created.isoformat()}",
            model_version_id=MODEL, scope=SCOPE,
            metrics={"brier": brier}, sample_size=285, created_at=created,
        ))
    s.commit()
    yield s
    s.close()
    db_engine.get_engine.cache_clear()  # type: ignore[attr-defined]


def _rows(session: Session) -> list[dict]:
    """Through the app, not by calling the endpoint function directly.

    The session now arrives by dependency injection, so calling the
    function by hand would either fail or require passing a session the
    real request never uses. Going through `TestClient` exercises the
    wiring production uses — including the dependency that returns the
    connection to the pool.
    """
    from fastapi.testclient import TestClient

    from fde_api.api.main import app

    with TestClient(app) as client:
        resp = client.get("/v1/performance/model-comparison")
    assert resp.status_code == 200, resp.text
    return [
        {"model": r["model_version_id"], "scope": r["scope"],
         "brier": (r["metrics"] or {}).get("brier")}
        for r in resp.json()["rows"]
    ]


class TestDuplicateEvaluationsComeBackInAStableOrder:
    def test_both_evaluations_are_returned(self, session: Session) -> None:
        """Neither is dropped. The earlier run happened and the record is
        append-only; the screen decides what to display, not the store."""
        rows = [r for r in _rows(session) if r["model"] == MODEL]
        assert len(rows) == 2

    def test_they_arrive_oldest_first(self, session: Session) -> None:
        """The screen keeps the LAST row it sees for a model+scope, so
        oldest-first means the latest evaluation is the one displayed."""
        rows = [r for r in _rows(session) if r["model"] == MODEL]
        assert rows[0]["brier"] == pytest.approx(0.21589059)
        assert rows[1]["brier"] == pytest.approx(0.21590024)

    def test_the_order_does_not_depend_on_insertion_order(
        self, session: Session
    ) -> None:
        """The fixture inserts latest-first. If the answer tracked
        insertion order, the superseded row would arrive last and be the
        one shown."""
        first, second = (r["brier"] for r in _rows(session) if r["model"] == MODEL)
        assert first < second, "oldest evaluation must not come back last"

    def test_repeated_calls_return_the_same_order(self, session: Session) -> None:
        """Arbitrary is the actual defect. Stability is the fix."""
        assert _rows(session) == _rows(session)
