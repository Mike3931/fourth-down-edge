"""Recovery-lineage races against a REAL PostgreSQL server.

`test_recovery_concurrency.py` races four threads, but against a
file-backed SQLite database. SQLite serialises writers at the file level,
so a race that passes there says nothing about PostgreSQL row locking. Its
result was reported as SQLite-only for exactly that reason.

This module is the PostgreSQL gate. Every test here:

  * runs against `FDE_DATABASE_URL` and FAILS if it is absent — there is no
    SQLite fallback, because a silently-downgraded race reported as passing
    is worse than a race that did not run;
  * asserts `engine.dialect.name == "postgresql"` before doing anything;
  * uses at least two GENUINELY independent connections, released together
    from a `threading.Barrier` so the contention is repeatable rather than
    probabilistic.

Every table used here is created and dropped per test in a dedicated
schema, so this never touches the migration-managed public schema the
other PostgreSQL jobs verify.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import OddsQuote, ScheduledJobRun
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.handlers import register_all
from fde_api.forward.policy import build_policy_draft, freeze_policy
from fde_api.forward.schedule import ingest_schedule
from fde_api.forward.scheduler import FrozenClock, Scheduler
from fde_api.forward.state import Outcome
from fde_api.forward.venues import seed_venues
from pgconftest import report_backend, require_pg_url

pytestmark = pytest.mark.pg_concurrency

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
NOW = KICK - timedelta(days=3)
WORKERS = 4

HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,"
    "home_score,location,result,total,overtime,old_game_id,gsis,nfl_detail_id,pfr,pff,espn,ftn,"
    "away_rest,home_rest,away_moneyline,home_moneyline,spread_line,away_spread_odds,"
    "home_spread_odds,total_line,under_odds,over_odds,div_game,roof,surface,temp,wind,"
    "away_qb_id,home_qb_id,away_qb_name,home_qb_name,away_coach,home_coach,referee,stadium_id,stadium"
)


def _csv() -> bytes:
    base = dict.fromkeys(HEADER.split(","), "")
    base.update({
        "game_id": "2026_02_KC_BUF", "season": "2026", "game_type": "REG", "week": "2",
        "gameday": "2026-09-13", "gametime": "13:00", "away_team": "KC", "home_team": "BUF",
        "location": "Home", "div_game": "0", "roof": "outdoors", "surface": "a_turf",
        "stadium_id": "BUF00", "stadium": "Highmark Stadium", "away_rest": "7", "home_rest": "7",
    })
    return ("\n".join([HEADER, ",".join(base[k] for k in HEADER.split(","))]) + "\n").encode()


def _payload() -> list[dict]:
    def book(key: str) -> dict:
        return {"key": key, "last_update": NOW.isoformat(), "markets": [
            {"key": "spreads", "outcomes": [
                {"name": "Buffalo Bills", "price": -110, "point": -2.5},
                {"name": "Kansas City Chiefs", "price": -110, "point": 2.5}]}]}
    return [{"id": "evt1", "commence_time": KICK.isoformat(),
             "home_team": "Buffalo Bills", "away_team": "Kansas City Chiefs",
             "bookmakers": [book("draftkings"), book("fanduel"), book("betmgm")]}]


@pytest.fixture(scope="module")
def engine():
    """Module-scoped PostgreSQL engine. Fails loudly without the URL."""
    from sqlalchemy import create_engine

    url = require_pg_url()
    eng = create_engine(url, future=True, pool_size=8, max_overflow=4, pool_pre_ping=True)
    assert eng.dialect.name == "postgresql", (
        f"refusing to run a PostgreSQL concurrency suite on {eng.dialect.name}"
    )
    report_backend(eng, "pg recovery concurrency")
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(engine, tmp_path, monkeypatch):
    """A clean schema per test, isolated from the migration-managed one."""
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    schema = f"rec_{abs(hash(tmp_path.name)) % 10**8}"
    with engine.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(eng)
    f = sessionmaker(bind=eng, future=True)
    with f() as s:
        seed_venues(s)
        freeze_policy(s, build_policy_draft(policy_version="ftp-2026-v1",
                                            start=NOW.date(), end=(KICK + timedelta(days=120)).date()))
        ingest_schedule(s, _csv(), season=2026, observed_at=NOW - timedelta(days=10))
        s.commit()
    yield f
    with engine.begin() as c:
        c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def _sched(factory):
    s = Scheduler(factory, clock=FrozenClock(NOW), cohort=Cohort.BURN_IN,
                  provider_mode=ProviderMode.FIXTURE, policy_version="ftp-2026-v1")
    register_all(s)
    return s


def _race(fn, n: int = WORKERS) -> list[Any]:
    """Release n threads simultaneously from a barrier.

    Each thread builds its OWN Scheduler, so each takes its own connection
    from the pool. A barrier makes the contention repeatable instead of
    depending on scheduling luck.
    """
    barrier = threading.Barrier(n)
    out: list[Any] = [None] * n

    def work(i: int) -> None:
        barrier.wait(timeout=30)
        try:
            out[i] = fn(i)
        except Exception as exc:
            out[i] = exc

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a worker deadlocked"
    return out


def _outcomes(results: list[Any]) -> list[dict]:
    """Assert every worker returned, and surface the real failure if not.

    `_race` captures worker exceptions into the result list so one thread
    cannot take the process down. That makes it possible to index a result
    that is actually an exception - `r["status"]` on a DataError raised
    `TypeError: 'DataError' object is not subscriptable`, which buried the
    genuine PostgreSQL error under a test-harness one. Every test funnels
    through here so the underlying exception is always what gets reported.
    """
    failures = [r for r in results if isinstance(r, BaseException)]
    if failures:
        detail = chr(10).join(f"  worker: {type(f).__name__}: {f}" for f in failures)
        raise AssertionError(f"{len(failures)}/{len(results)} workers raised:{chr(10)}{detail}")
    missing = [i for i, r in enumerate(results) if r is None]
    assert not missing, f"workers {missing} never produced a result"
    return list(results)


def _runs(factory) -> list[ScheduledJobRun]:
    with factory() as s:
        rows = list(s.scalars(select(ScheduledJobRun).order_by(ScheduledJobRun.recovery_sequence)))
        for r in rows:
            s.expunge(r)
        return rows


def _quotes(factory) -> int:
    with factory() as s:
        return s.scalar(select(func.count(OddsQuote.id))) or 0


def _crash_after_commit(factory) -> int:
    s = _sched(factory)
    s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload()})
    written = _quotes(factory)
    with factory() as sess:
        run = sess.scalars(select(ScheduledJobRun)).one()
        run.job_outcome = Outcome.RUNNING.value
        run.status = "running"
        run.completed_at = None
        sess.commit()
    return written


class TestBackendIsReallyPostgres:
    def test_dialect_is_postgresql(self, engine) -> None:
        assert engine.dialect.name == "postgresql"

    def test_the_suite_never_constructs_sqlite(self) -> None:
        """A source-level guard: this module must not build a SQLite URL."""
        from pathlib import Path

        src = Path(__file__).read_text(encoding="utf-8")
        code = [ln for ln in src.splitlines() if not ln.strip().startswith(("#", "*"))]
        assert not any("sqlite://" in ln for ln in code if "assert" not in ln)

    def test_sessions_use_independent_connections(self, factory) -> None:
        """Two sessions must hold two different backend PIDs, or the races
        below would be running on one connection and proving nothing."""
        with factory() as a, factory() as b:
            pid_a = a.execute(text("SELECT pg_backend_pid()")).scalar()
            pid_b = b.execute(text("SELECT pg_backend_pid()")).scalar()
        assert pid_a != pid_b, (pid_a, pid_b)


class TestInitialRunRace:
    def test_exactly_one_active_run_and_no_duplicate_effects(self, factory) -> None:
        results = _race(lambda i: _sched(factory).run_job(
            "odds_capture", slot=NOW, params={"fixture_payload": _payload()}))
        statuses = [r["status"] for r in _outcomes(results)]
        assert statuses.count("finished") == 1, statuses
        assert statuses.count("skipped") == WORKERS - 1, statuses
        assert len(_runs(factory)) == 1

    def test_losers_receive_a_typed_skip(self, factory) -> None:
        results = _race(lambda i: _sched(factory).run_job(
            "odds_capture", slot=NOW, params={"fixture_payload": _payload()}))
        for r in _outcomes(results):
            assert r["status"] in {"finished", "skipped", "refused"}, r["status"]

    def test_only_one_worker_wrote_domain_effects(self, factory) -> None:
        _race(lambda i: _sched(factory).run_job(
            "odds_capture", slot=NOW, params={"fixture_payload": _payload()}))
        single = _quotes(factory)
        # A second, uncontended run must add nothing: the effects are complete.
        _sched(factory).run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload()})
        assert _quotes(factory) == single


class TestRecoveryRace:
    def _restart(self, factory):
        def go(_i: int) -> dict:
            s = _sched(factory)
            s.reconcile_startup()
            return s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload()})
        return go

    def test_exactly_one_recovery_branch(self, factory) -> None:
        written = _crash_after_commit(factory)
        results = _race(self._restart(factory))
        _outcomes(results)

        runs = _runs(factory)
        recoveries = [r for r in runs if (r.recovery_sequence or 0) > 0]
        assert len(recoveries) == 1, [r.idempotency_key for r in runs]
        assert recoveries[0].recovery_sequence == 1
        assert _quotes(factory) == written

    def test_sequence_is_monotonic_with_no_gaps(self, factory) -> None:
        _crash_after_commit(factory)
        _race(self._restart(factory))
        seqs = sorted((r.recovery_sequence or 0) for r in _runs(factory))
        assert seqs == list(range(len(seqs))), seqs

    def test_no_parallel_branch_and_one_root(self, factory) -> None:
        _crash_after_commit(factory)
        _race(self._restart(factory))
        runs = _runs(factory)
        assert len({r.root_run_id for r in runs}) == 1
        # At most one member may point at any given predecessor.
        preds = [r.recovery_of_run_id for r in runs if r.recovery_of_run_id]
        assert len(preds) == len(set(preds)), preds


class TestAdministrativeRecoveryRace:
    def test_at_most_one_override_succeeds_and_is_attributed(self, factory) -> None:
        _crash_after_commit(factory)
        with factory() as sess:
            run = sess.scalars(select(ScheduledJobRun)).one()
            run.job_outcome = Outcome.INTERRUPTED.value
            run.status = "interrupted"
            sess.commit()

        def force(i: int) -> dict:
            return _sched(factory).run_job(
                "odds_capture", slot=NOW, params={"fixture_payload": _payload()},
                administrative_override=True,
                override_operator=f"operator-{i}",
                override_reason="pg administrative race test",
            )

        results = _race(force)
        _outcomes(results)

        overrides = [r for r in _runs(factory) if r.administrative_override]
        assert len(overrides) <= 1, [r.override_operator for r in overrides]
        if overrides:
            assert overrides[0].override_operator
            assert overrides[0].override_reason


class TestInitialVersusRecoveryRace:
    def test_only_one_active_successor(self, factory) -> None:
        """One ordinary recovery against one administrative override."""
        _crash_after_commit(factory)

        def go(i: int) -> dict:
            s = _sched(factory)
            s.reconcile_startup()
            if i % 2 == 0:
                return s.run_job("odds_capture", slot=NOW,
                                 params={"fixture_payload": _payload()})
            return s.run_job(
                "odds_capture", slot=NOW, params={"fixture_payload": _payload()},
                administrative_override=True,
                override_operator=f"operator-{i}",
                override_reason="mixed race test",
            )

        results = _race(go)
        _outcomes(results)

        runs = _runs(factory)
        successors = [r for r in runs if (r.recovery_sequence or 0) == 1]
        assert len(successors) == 1, [r.idempotency_key for r in successors]
        assert len({r.root_run_id for r in runs}) == 1
