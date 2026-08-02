"""Two workers, one slot: what happens when recovery races itself.

The sequential tests prove the logic; these prove the guard. A restart after
a crash is exactly when several workers are most likely to come up at once,
so "exactly one recovery" has to hold under real concurrency, not just when
calls happen to be ordered.

These use a file-backed SQLite database rather than the in-memory one used
elsewhere: threads need independent connections to a shared database, which
`sqlite://` cannot provide. A barrier aligns the threads so they contend
rather than merely running one after another.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
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


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    from fde_api.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent.db'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        # WAL lets readers proceed while a writer holds the lock, which is
        # what makes contention here look like a real deployment instead of
        # a queue of serialized writes.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, future=True)
    with f() as s:
        seed_venues(s)
        freeze_policy(s, build_policy_draft(policy_version="ftp-2026-v1",
                                            start=date(2026, 9, 1), end=date(2027, 2, 28)))
        ingest_schedule(s, _csv(), season=2026, observed_at=NOW - timedelta(days=10))
        s.commit()
    yield f
    engine.dispose()


def _sched(factory):
    s = Scheduler(factory, clock=FrozenClock(NOW), cohort=Cohort.BURN_IN,
                  provider_mode=ProviderMode.FIXTURE, policy_version="ftp-2026-v1")
    register_all(s)
    return s


def _race(fn, n: int = WORKERS) -> list:
    """Run fn(i) on n threads released simultaneously. Returns results in
    completion-independent index order; exceptions are returned, not raised,
    so a single worker failing does not hide the others' outcomes."""
    barrier = threading.Barrier(n)
    out: list = [None] * n

    def work(i: int) -> None:
        barrier.wait()
        try:
            out[i] = fn(i)
        except Exception as exc:
            out[i] = exc

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "worker deadlocked"
    return out


def _runs(factory) -> list[ScheduledJobRun]:
    with factory() as s:
        rows = list(s.scalars(
            select(ScheduledJobRun).order_by(ScheduledJobRun.recovery_sequence)
        ))
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


class TestConcurrentFreshClaim:
    def test_only_one_worker_claims_a_slot(self, factory) -> None:
        results = _race(lambda i: _sched(factory).run_job(
            "odds_capture", slot=NOW, params={"fixture_payload": _payload()}))
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, errors
        statuses = [r["status"] for r in results]
        assert statuses.count("finished") == 1, statuses
        assert statuses.count("skipped") == WORKERS - 1, statuses
        assert len(_runs(factory)) == 1


class TestConcurrentRecovery:
    def test_only_one_recovery_row_is_created(self, factory) -> None:
        """Four workers restart at once against one interrupted slot."""
        written = _crash_after_commit(factory)

        def restart(_i: int) -> dict:
            s = _sched(factory)
            s.reconcile_startup()
            return s.run_job("odds_capture", slot=NOW,
                             params={"fixture_payload": _payload()})

        results = _race(restart)
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, errors

        runs = _runs(factory)
        recoveries = [r for r in runs if (r.recovery_sequence or 0) > 0]
        assert len(recoveries) == 1, [r.idempotency_key for r in runs]
        assert recoveries[0].recovery_sequence == 1
        assert _quotes(factory) == written  # no duplicated domain rows

    def test_losers_do_not_report_success(self, factory) -> None:
        """A worker that lost the race must not claim it recovered anything."""
        _crash_after_commit(factory)

        def restart(_i: int) -> dict:
            s = _sched(factory)
            s.reconcile_startup()
            return s.run_job("odds_capture", slot=NOW,
                             params={"fixture_payload": _payload()})

        results = _race(restart)
        finished = [r for r in results if r["status"] == "finished"]
        assert len(finished) == 1
        for r in results:
            if r["status"] != "finished":
                assert r["status"] in {"skipped", "refused", "manual_review_required"}
                assert r.get("recovery_sequence") in (None, 0)

    def test_chain_stays_single_rooted_under_contention(self, factory) -> None:
        """Contention must not fork the chain into two roots."""
        _crash_after_commit(factory)

        def restart(_i: int) -> dict:
            s = _sched(factory)
            s.reconcile_startup()
            return s.run_job("odds_capture", slot=NOW,
                             params={"fixture_payload": _payload()})

        _race(restart)
        runs = _runs(factory)
        assert len({r.root_run_id for r in runs}) == 1
        sequences = sorted(r.recovery_sequence or 0 for r in runs)
        assert sequences == list(range(len(runs))), sequences  # no gaps, no dupes


class TestConcurrentReconciliation:
    def test_reconcile_is_safe_to_run_from_several_workers(self, factory) -> None:
        """Every worker calls reconcile_startup; the row ends INTERRUPTED once."""
        _crash_after_commit(factory)
        results = _race(lambda _i: _sched(factory).reconcile_startup())
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, errors
        # Exactly one worker observes the transition; the rest find nothing
        # left to reconcile. Totals across workers must not exceed one.
        assert sum(r["count"] for r in results) == 1

        runs = _runs(factory)
        assert len(runs) == 1
        assert runs[0].job_outcome == Outcome.INTERRUPTED.value
        assert runs[0].status == "interrupted"


class TestConcurrentAdministrativeOverride:
    def test_only_one_override_takes_effect(self, factory) -> None:
        """Several operators forcing the same slot must not stack overrides."""
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
                override_reason="forced during concurrency test",
            )

        results = _race(force)
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, errors

        runs = _runs(factory)
        overrides = [r for r in runs if r.administrative_override]
        assert len(overrides) <= 1, [r.override_operator for r in overrides]
        if overrides:
            # Whoever won must be fully attributed - an override with no
            # operator or no reason is an unauditable one.
            assert overrides[0].override_operator
            assert overrides[0].override_reason
            assert overrides[0].override_operator.startswith("operator-")
