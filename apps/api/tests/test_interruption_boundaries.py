"""Interruption boundaries: what survives a crash, and what recovery does.

The five boundaries that matter, in order of increasing subtlety:

  1. before the handler is invoked           - nothing happened
  2. before the domain write                 - nothing happened
  3. after the write, before commit          - the write must VANISH
  4. after commit, before run finalization   - the write must be FOUND
  5. during finalization                     - same as 4

Boundary 4 is the dangerous one: the run row says "running" while real
records exist. Anything that decides replay from run status alone gets
this wrong, which is why the decision is made by inspecting the domain.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import OddsQuote, ScheduledJobRun
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.handlers import register_all
from fde_api.forward.policy import build_policy_draft, freeze_policy
from fde_api.forward.recovery import ReplayDecision, chain_members, inspect_prior_effects
from fde_api.forward.schedule import ingest_schedule
from fde_api.forward.scheduler import FrozenClock, JobSkipped, Scheduler
from fde_api.forward.state import Outcome
from fde_api.forward.venues import seed_venues

KICK = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
NOW = KICK - timedelta(days=3)

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
    row = ",".join(base[k] for k in HEADER.split(","))
    return ("\n".join([HEADER, row]) + "\n").encode()


def _payload(ts: datetime) -> list[dict]:
    def book(key: str) -> dict:
        return {"key": key, "last_update": ts.isoformat(), "markets": [
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
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, future=True)
    with f() as s:
        seed_venues(s)
        freeze_policy(s, build_policy_draft(policy_version="ftp-2026-v1",
                                            start=date(2026, 9, 1), end=date(2027, 2, 28)))
        ingest_schedule(s, _csv(), season=2026, observed_at=NOW - timedelta(days=10))
        s.commit()
    return f


def _sched(factory, clock=None):
    s = Scheduler(factory, clock=clock or FrozenClock(NOW), cohort=Cohort.BURN_IN,
                  provider_mode=ProviderMode.FIXTURE, policy_version="ftp-2026-v1")
    register_all(s)
    return s


def _quotes(factory) -> int:
    with factory() as s:
        return s.scalar(select(func.count(OddsQuote.id))) or 0


def _runs(factory) -> list[ScheduledJobRun]:
    with factory() as s:
        rows = list(s.scalars(select(ScheduledJobRun).order_by(ScheduledJobRun.created_at)))
        for r in rows:
            s.expunge(r)
        return rows


class TestBoundary1And2_NothingHappened:
    def test_interruption_before_handler_invocation(self, factory) -> None:
        """No handler ran, so no effects and a clean replay decision."""
        with factory() as sess:
            insp = inspect_prior_effects(sess, job_kind="odds_capture", idempotency_key="never-ran")
        assert insp.actual_effect_count == 0
        assert insp.recommended_decision is ReplayDecision.NO_PRIOR_EFFECTS_REPLAY
        assert _quotes(factory) == 0

    def test_interruption_before_domain_write(self, factory) -> None:
        """Handler started but raised before writing anything."""
        s = _sched(factory)

        def die_early(ctx):
            raise RuntimeError("died before writing")

        s.jobs["odds_capture"].handler = die_early
        s.jobs["odds_capture"].retry.max_attempts = 1
        r = s.run_job("odds_capture", slot=NOW)
        assert r["status"] == "dead_letter"
        assert _quotes(factory) == 0


class TestBoundary3_RollbackDiscardsUncommitted:
    def test_uncommitted_write_vanishes(self, factory) -> None:
        """A write followed by an exception must leave nothing behind."""
        s = _sched(factory)

        def write_then_die(ctx):
            from fde_api.forward.odds import capture_odds

            capture_odds(ctx.session, _payload(NOW), request_id=ctx.idempotency_key,
                         data_mode=ctx.data_mode, observed_at=ctx.now())
            # The rows exist in this transaction...
            assert ctx.session.scalar(select(func.count(OddsQuote.id))) > 0
            raise RuntimeError("crash before commit")

        s.jobs["odds_capture"].handler = write_then_die
        s.jobs["odds_capture"].retry.max_attempts = 1
        s.run_job("odds_capture", slot=NOW)
        # ...but the transaction rolled back, so nothing survives.
        assert _quotes(factory) == 0

    def test_rollback_leaves_no_partial_effects_for_recovery_to_find(self, factory) -> None:
        s = _sched(factory)

        def write_then_die(ctx):
            from fde_api.forward.odds import capture_odds

            capture_odds(ctx.session, _payload(NOW), request_id=ctx.idempotency_key,
                         data_mode=ctx.data_mode, observed_at=ctx.now())
            raise RuntimeError("crash before commit")

        s.jobs["odds_capture"].handler = write_then_die
        s.jobs["odds_capture"].retry.max_attempts = 1
        s.run_job("odds_capture", slot=NOW)
        with factory() as sess:
            insp = inspect_prior_effects(sess, job_kind="odds_capture",
                                         idempotency_key="odds_capture:burn_in:x")
        assert insp.recommended_decision is ReplayDecision.NO_PRIOR_EFFECTS_REPLAY


class TestBoundary4_CommittedEffectsAreFound:
    """The dangerous case: run says running, records exist."""

    def _crash_after_commit(self, factory) -> int:
        s = _sched(factory)
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload(NOW)})
        written = _quotes(factory)
        # Simulate dying after commit but before finalization.
        with factory() as sess:
            run = sess.scalars(select(ScheduledJobRun)).one()
            run.job_outcome = Outcome.RUNNING.value
            run.status = "running"
            run.completed_at = None
            sess.commit()
        return written

    def test_committed_effects_are_discovered(self, factory) -> None:
        written = self._crash_after_commit(factory)
        assert written > 0
        with factory() as sess:
            run = sess.scalars(select(ScheduledJobRun)).one()
            insp = inspect_prior_effects(sess, job_kind="odds_capture",
                                         idempotency_key=run.idempotency_key)
        assert insp.actual_effect_count == written
        assert insp.recommended_decision is ReplayDecision.PRIOR_EFFECTS_IDEMPOTENT_REPLAY

    def test_restart_reconciles_then_recovers_without_duplicating(self, factory) -> None:
        written = self._crash_after_commit(factory)

        s2 = _sched(factory)
        assert s2.reconcile_startup()["count"] == 1

        r = s2.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload(NOW)})
        assert r["status"] == "finished"
        assert _quotes(factory) == written  # no duplicates
        assert r["recovery_sequence"] == 1
        assert r["replay_decision"] == ReplayDecision.PRIOR_EFFECTS_IDEMPOTENT_REPLAY.value

    def test_recovery_row_carries_full_lineage(self, factory) -> None:
        self._crash_after_commit(factory)
        s2 = _sched(factory)
        s2.reconcile_startup()
        s2.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload(NOW)})

        runs = _runs(factory)
        assert len(runs) == 2
        root, recovery = runs[0], runs[1]
        assert recovery.root_run_id == root.id
        assert recovery.recovery_of_run_id == root.id
        assert recovery.recovery_sequence == 1
        assert recovery.logical_slot == root.logical_slot
        assert recovery.original_idempotency_key == root.original_idempotency_key
        assert recovery.recovery_reason
        assert recovery.reconciled_at is not None
        assert recovery.prior_effects_detected is True
        assert recovery.replay_decision == ReplayDecision.PRIOR_EFFECTS_IDEMPOTENT_REPLAY.value
        assert recovery.administrative_override is False
        assert recovery.state_origin == "LIVE"

    def test_chain_is_queryable_chronologically(self, factory) -> None:
        self._crash_after_commit(factory)
        s2 = _sched(factory)
        s2.reconcile_startup()
        s2.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload(NOW)})
        runs = _runs(factory)
        with factory() as sess:
            chain = chain_members(sess, runs[0].id)
        assert [m.recovery_sequence for m in chain] == [0, 1]


class TestTwoConsecutiveInterruptions:
    def test_second_recovery_increments_to_two(self, factory) -> None:
        s = _sched(factory)
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload(NOW)})
        written = _quotes(factory)

        for _ in range(2):
            with factory() as sess:
                # Order by sequence, not created_at: a frozen clock gives every
                # member of the chain the same timestamp.
                latest = sess.scalars(
                    select(ScheduledJobRun).order_by(ScheduledJobRun.recovery_sequence.desc())
                ).first()
                latest.job_outcome = Outcome.RUNNING.value
                latest.status = "running"
                latest.completed_at = None
                sess.commit()
            s2 = _sched(factory)
            s2.reconcile_startup()
            r = s2.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload(NOW)})
            # Every recovery in the chain must still see the original effects.
            # Inspection keys off the chain's stable key, so a recovery whose
            # own writes were no-ops does not conclude "nothing happened".
            assert r["replay_decision"] == ReplayDecision.PRIOR_EFFECTS_IDEMPOTENT_REPLAY.value

        runs = _runs(factory)
        assert [r.recovery_sequence for r in runs] == [0, 1, 2]
        assert len({r.root_run_id for r in runs}) == 1  # one chain
        assert _quotes(factory) == written  # still no duplicates


class TestFinalStateIsAuthoritative:
    def test_recovered_run_records_both_axes(self, factory) -> None:
        s = _sched(factory)
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload(NOW)})
        with factory() as sess:
            run = sess.scalars(select(ScheduledJobRun)).one()
            run.job_outcome = Outcome.RUNNING.value
            run.status = "running"
            sess.commit()
        s2 = _sched(factory)
        s2.reconcile_startup()
        s2.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload(NOW)})

        runs = _runs(factory)
        for r in runs:
            assert r.job_outcome is not None
            assert r.domain_state is not None
            assert r.state_origin == "LIVE"
        # The interrupted root keeps its INTERRUPTED outcome as history.
        assert runs[0].job_outcome == Outcome.INTERRUPTED.value
        assert runs[0].status == "interrupted"  # projection followed


class TestNoRecoveryWhenNotInterrupted:
    def test_completed_run_is_not_recovered(self, factory) -> None:
        s = _sched(factory)
        s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload(NOW)})
        r = s.run_job("odds_capture", slot=NOW, params={"fixture_payload": _payload(NOW)})
        assert r["status"] == "skipped"
        assert len(_runs(factory)) == 1  # no recovery row created

    def test_skipped_run_is_not_recovered(self, factory) -> None:
        s = _sched(factory)

        def skip(ctx):
            raise JobSkipped("nothing to do")

        s.jobs["odds_capture"].handler = skip
        s.run_job("odds_capture", slot=NOW)
        r = s.run_job("odds_capture", slot=NOW)
        assert r["status"] == "skipped"
        assert len(_runs(factory)) == 1
