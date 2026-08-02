"""Scheduler tests: idempotency, restart recovery, retries, locking."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from fde_api.db.forward_models import ScheduledJobRun
from fde_api.db.models import Base
from fde_api.forward.cohort import Cohort, ProviderMode
from fde_api.forward.quota import (
    QuotaConfig,
    budget_report,
    forecast_from_cadence,
    scheduler_cadence_tiers,
)
from fde_api.forward.scheduler import (
    CatchUpPolicy,
    FrozenClock,
    JobDefinition,
    JobResult,
    JobSkipped,
    JobStatus,
    RetryPolicy,
    Scheduler,
    make_idempotency_key,
    slot_for,
)

T0 = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


@pytest.fixture()
def factory():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _sched(factory, clock=None, **kw):
    return Scheduler(
        factory,
        clock=clock or FrozenClock(T0),
        cohort=Cohort.BURN_IN,
        provider_mode=ProviderMode.FIXTURE,
        policy_version="ftp-2026-v1",
        rng=random.Random(1),
        **kw,
    )


def _counting(bucket, result=None):
    def handler(ctx):
        bucket.append(ctx.attempt)
        return result or JobResult()

    return handler


class TestSlotsAndKeys:
    def test_slot_floors_to_interval(self) -> None:
        i = timedelta(minutes=5)
        a = slot_for(i, datetime(2026, 9, 13, 12, 3, 40, tzinfo=UTC))
        b = slot_for(i, datetime(2026, 9, 13, 12, 4, 59, tzinfo=UTC))
        assert a == b  # same logical slot -> same key -> one execution

    def test_key_includes_job_cohort_and_slot(self) -> None:
        k = make_idempotency_key(job_name="odds_capture", slot=T0, cohort=Cohort.BURN_IN)
        assert "odds_capture" in k and "burn_in" in k and "20260913T120000Z" in k

    def test_cohorts_get_distinct_keys(self) -> None:
        a = make_idempotency_key(job_name="j", slot=T0, cohort=Cohort.BURN_IN)
        b = make_idempotency_key(job_name="j", slot=T0, cohort=Cohort.OFFICIAL_FORWARD_TEST)
        assert a != b


class TestIdempotency:
    def test_same_slot_runs_once(self, factory) -> None:
        calls: list[int] = []
        s = _sched(factory)
        s.register(JobDefinition(name="cap", handler=_counting(calls), interval=timedelta(minutes=5)))
        first = s.run_job("cap", slot=T0)
        second = s.run_job("cap", slot=T0)
        assert first["status"] == "finished"
        assert second["status"] == "skipped" and "already completed" in second["reason"]
        assert len(calls) == 1

    def test_different_slots_both_run(self, factory) -> None:
        calls: list[int] = []
        s = _sched(factory)
        s.register(JobDefinition(name="cap", handler=_counting(calls), interval=timedelta(minutes=5)))
        s.run_job("cap", slot=T0)
        s.run_job("cap", slot=T0 + timedelta(minutes=5))
        assert len(calls) == 2

    def test_params_disambiguate_keys(self, factory) -> None:
        seen: list[dict] = []

        def handler(ctx):
            seen.append(ctx.params)
            return JobResult()

        s = _sched(factory)
        s.register(JobDefinition(name="wx", handler=handler, interval=timedelta(hours=1)))
        s.run_job("wx", slot=T0, params={"game": "A"})
        s.run_job("wx", slot=T0, params={"game": "B"})
        assert len(seen) == 2

    def test_concurrent_claim_is_prevented(self, factory) -> None:
        """Two schedulers, same slot: the UNIQUE key lets exactly one win."""
        calls: list[int] = []
        a, b = _sched(factory), _sched(factory)
        for s in (a, b):
            s.register(JobDefinition(name="cap", handler=_counting(calls), interval=timedelta(minutes=5)))
        r1 = a.run_job("cap", slot=T0)
        r2 = b.run_job("cap", slot=T0)
        assert r1["status"] == "finished" and r2["status"] == "skipped"
        assert len(calls) == 1


class TestRetriesAndFailure:
    def test_transient_failure_retries_then_succeeds(self, factory) -> None:
        attempts: list[int] = []

        def flaky(ctx):
            attempts.append(ctx.attempt)
            if ctx.attempt < 3:
                raise RuntimeError("provider hiccup")
            return JobResult(records_written=2)

        s = _sched(factory)
        s.register(JobDefinition(name="odds", handler=flaky, interval=timedelta(minutes=5),
                                 retry=RetryPolicy(max_attempts=4, base_seconds=1)))
        r = s.run_job("odds", slot=T0)
        assert r["status"] == "finished" and attempts == [1, 2, 3]

    def test_persistent_failure_dead_letters(self, factory) -> None:
        def always_fail(ctx):
            raise RuntimeError("provider down")

        s = _sched(factory)
        s.register(JobDefinition(name="odds", handler=always_fail, interval=timedelta(minutes=5),
                                 retry=RetryPolicy(max_attempts=2, base_seconds=1)))
        r = s.run_job("odds", slot=T0)
        assert r["status"] == "dead_letter" and "provider down" in r["error"]

    def test_failed_attempts_are_retained_as_history(self, factory) -> None:
        def always_fail(ctx):
            raise RuntimeError("boom")

        s = _sched(factory)
        s.register(JobDefinition(name="j", handler=always_fail, interval=timedelta(minutes=5),
                                 retry=RetryPolicy(max_attempts=3, base_seconds=1)))
        s.run_job("j", slot=T0)
        with factory() as sess:
            rows = sess.scalars(select(ScheduledJobRun)).all()
        assert len(rows) == 3  # every attempt preserved
        assert rows[-1].status == JobStatus.DEAD_LETTER.value

    def test_backoff_is_bounded_and_jittered(self) -> None:
        p = RetryPolicy(base_seconds=30, max_seconds=900, jitter_ratio=0.25)
        rng = random.Random(7)
        d1, d5 = p.delay_for(1, rng), p.delay_for(5, rng)
        assert 0 < d1 <= 30 * 1.25
        assert d5 <= 900 * 1.25  # bounded

    def test_job_can_skip_deliberately(self, factory) -> None:
        def skip(ctx):
            raise JobSkipped("no eligible games")

        s = _sched(factory)
        s.register(JobDefinition(name="j", handler=skip, interval=timedelta(minutes=5)))
        r = s.run_job("j", slot=T0)
        assert r["status"] == "skipped" and "no eligible games" in r["reason"]


class TestRestartRecovery:
    def test_interrupted_runs_reconciled_on_startup(self, factory) -> None:
        s = _sched(factory)
        with factory() as sess:  # simulate a crash: row left `running`
            sess.add(ScheduledJobRun(
                id="run_crashed", job_kind="odds_capture", idempotency_key="odds_capture:burn_in:X",
                data_mode="LIVE_RESEARCH", scheduled_for=T0, started_at=T0,
                status=JobStatus.RUNNING.value, job_outcome="RUNNING",
                state_origin="LIVE", root_run_id="run_crashed", recovery_sequence=0,
                administrative_override=False, retry_count=0, provider_calls=0,
                records_received=0, records_written=0, code_commit="abc", created_at=T0))
            sess.commit()
        out = s.reconcile_startup()
        assert out["count"] == 1
        with factory() as sess:
            row = sess.get(ScheduledJobRun, "run_crashed")
        assert row.job_outcome == "INTERRUPTED"
        assert row.status == JobStatus.INTERRUPTED.value  # projection follows
        assert "terminated" in row.error_summary

    def test_restart_does_not_duplicate_completed_work(self, factory) -> None:
        calls: list[int] = []
        clock = FrozenClock(T0)
        s1 = _sched(factory, clock=clock)
        s1.register(JobDefinition(name="cap", handler=_counting(calls), interval=timedelta(minutes=5)))
        s1.run_job("cap", slot=T0)
        s2 = _sched(factory, clock=clock)  # "restart" over the same database
        s2.register(JobDefinition(name="cap", handler=_counting(calls), interval=timedelta(minutes=5)))
        s2.reconcile_startup()
        r = s2.run_job("cap", slot=T0)
        assert r["status"] == "skipped" and len(calls) == 1

    def test_schedules_survive_restart(self, factory) -> None:
        s1 = _sched(factory)
        s1.register(JobDefinition(name="cap", handler=lambda c: JobResult(), interval=timedelta(minutes=5)))
        s1.run_job("cap", slot=T0)
        s2 = _sched(factory)
        s2.register(JobDefinition(name="cap", handler=lambda c: JobResult(), interval=timedelta(minutes=5)))
        assert s2.health()["runs_by_status"].get("finished") == 1  # history persisted


class TestGracefulShutdown:
    def test_tick_stops_starting_jobs_after_shutdown(self, factory) -> None:
        calls: list[int] = []
        s = _sched(factory)
        s.register(JobDefinition(name="a", handler=_counting(calls), interval=timedelta(minutes=5)))
        s.request_shutdown()
        assert s.tick() == [] and calls == []
        assert s.health()["shutting_down"] is True


class TestMissedRuns:
    def test_missed_slots_detected(self, factory) -> None:
        s = _sched(factory, clock=FrozenClock(T0))
        s.register(JobDefinition(name="cap", handler=lambda c: JobResult(),
                                 interval=timedelta(hours=1), catch_up=CatchUpPolicy.SKIP))
        missed = s.missed_runs(lookback=timedelta(hours=3))
        assert len(missed) >= 2
        assert all(m["job"] == "cap" for m in missed)
        assert missed[0]["catch_up"] == "skip"


class TestRunRecord:
    def test_run_records_required_metadata(self, factory) -> None:
        s = _sched(factory)
        s.register(JobDefinition(
            name="odds_capture",
            handler=lambda c: JobResult(records_read=10, records_written=4, provider_calls=1,
                                        provider_credits=3, warnings=["one book missing"]),
            interval=timedelta(minutes=5)))
        s.run_job("odds_capture", slot=T0)
        with factory() as sess:
            row = sess.scalars(select(ScheduledJobRun)).one()
        assert row.job_kind == "odds_capture"
        assert row.scheduled_for == T0
        assert row.started_at is not None and row.completed_at is not None
        assert row.status == JobStatus.FINISHED.value
        assert row.records_received == 10 and row.records_written == 4
        assert row.provider_calls == 1 and row.code_commit
        assert "one book missing" in row.error_summary


class TestHealth:
    def test_health_reports_mode_and_cohort(self, factory) -> None:
        s = _sched(factory)
        s.register(JobDefinition(name="j", handler=lambda c: JobResult(), interval=timedelta(minutes=5)))
        h = s.health()
        assert h["cohort"] == "burn_in"
        assert h["provider_mode"] == "FIXTURE"
        assert h["policy_version"] == "ftp-2026-v1"
        assert "j" in h["registered_jobs"]


class TestQuotaForecastMatchesCadence:
    def test_forecast_generated_from_scheduler_cadence(self) -> None:
        assert forecast_from_cadence(scheduler_cadence_tiers()) == budget_report()

    def test_forecast_tracks_a_cadence_change(self) -> None:
        base = forecast_from_cadence(scheduler_cadence_tiers())
        faster_tiers = [(name, hours, max(1, mins // 2)) for name, hours, mins in scheduler_cadence_tiers()]
        faster = forecast_from_cadence(faster_tiers)
        assert faster["monthly_requirement_credits"] > base["monthly_requirement_credits"]

    def test_credits_per_request_reflected(self) -> None:
        one = forecast_from_cadence(scheduler_cadence_tiers(), QuotaConfig(credits_per_request=1))
        three = forecast_from_cadence(scheduler_cadence_tiers(), QuotaConfig(credits_per_request=3))
        assert three["week_1_credits"] == pytest.approx(one["week_1_credits"] * 3, rel=0.01)

    def test_closing_capture_language_is_qualified(self) -> None:
        note = budget_report()["closing_capture_note"]
        assert "highest scheduling and quota priority" in note
        assert "remain subject to provider availability" in note
        assert "unconditional" not in note.lower()
