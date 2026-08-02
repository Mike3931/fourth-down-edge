"""Cadence tables must not drift apart from the jobs they describe.

docs/forward-test.md claimed "a test asserts the two cannot drift apart".
No such test existed, and they had already drifted: consensus_build had
no entry in JOB_CADENCE at all and silently inherited an hourly fallback
while the quotes it summarises refresh every five minutes.

These are the assertions that claim actually requires.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fde_api.forward.handlers import HANDLERS, register_all
from fde_api.forward.quota import budget_report, scheduler_cadence_tiers
from fde_api.forward.scheduler import (
    CRITICAL_JOBS,
    JOB_CADENCE,
    JOB_CATCH_UP,
    JobDefinition,
)


class _Recorder:
    """Minimal scheduler stand-in that records what register_all asks for."""

    def __init__(self) -> None:
        self.jobs: dict[str, JobDefinition] = {}

    def register(self, job: JobDefinition) -> None:
        self.jobs[job.name] = job


class TestEveryJobDeclaresItsCadence:
    def test_no_handler_is_missing_from_the_cadence_table(self) -> None:
        missing = sorted(set(HANDLERS) - set(JOB_CADENCE))
        assert not missing, f"jobs with no declared cadence: {missing}"

    def test_no_cadence_entry_describes_a_job_that_does_not_exist(self) -> None:
        orphans = sorted(set(JOB_CADENCE) - set(HANDLERS))
        assert not orphans, f"cadence declared for unregistered jobs: {orphans}"

    def test_registration_refuses_a_job_with_no_cadence(self, monkeypatch) -> None:
        """The structural guard: a missing cadence is a configuration error,
        not a job that quietly runs hourly."""
        monkeypatch.setitem(HANDLERS, "invented_job", lambda ctx: None)
        with pytest.raises(ValueError, match="no declared cadence"):
            register_all(_Recorder())

    def test_registered_jobs_carry_the_declared_interval(self) -> None:
        r = _Recorder()
        register_all(r)
        for name, job in r.jobs.items():
            assert job.interval == JOB_CADENCE[name], name


class TestCadenceValuesAreSane:
    def test_every_cadence_is_positive(self) -> None:
        for name, interval in JOB_CADENCE.items():
            assert interval > timedelta(0), name

    def test_consensus_keeps_up_with_capture(self) -> None:
        """A consensus slower than the quotes it summarises means vintages
        are built on a stale market picture. This is the specific drift
        that went unnoticed."""
        assert JOB_CADENCE["consensus_build"] <= JOB_CADENCE["odds_capture"]

    def test_closing_capture_is_the_finest_cadence(self) -> None:
        """It is the only observation that cannot be recovered later."""
        assert JOB_CADENCE["closing_capture"] == min(JOB_CADENCE.values())

    def test_critical_jobs_all_have_a_declared_cadence(self) -> None:
        assert set(JOB_CADENCE) >= CRITICAL_JOBS

    def test_catch_up_policies_reference_real_jobs(self) -> None:
        orphans = sorted(set(JOB_CATCH_UP) - set(HANDLERS))
        assert not orphans, orphans


class TestForecastAndSchedulerRelationship:
    """The forecast tiers and JOB_CADENCE are two different models, and the
    documentation used to claim they were one. What can honestly be
    asserted is that the forecast never assumes LESS polling than the
    scheduler will actually attempt - otherwise the budget understates
    real credit use, which is the dangerous direction."""

    def test_forecast_tier_intervals_are_positive_and_ordered(self) -> None:
        tiers = scheduler_cadence_tiers()
        assert tiers
        intervals = [minutes for _, _, minutes in tiers]
        assert all(m > 0 for m in intervals)
        # Tiers run from distant to imminent, so polling gets finer.
        assert intervals == sorted(intervals, reverse=True), intervals

    def test_forecast_never_assumes_less_polling_than_the_scheduler(self) -> None:
        """The finest forecast tier must be at least as frequent as the
        odds_capture interval, or the budget understates credit use."""
        finest = min(minutes for _, _, minutes in scheduler_cadence_tiers())
        capture_minutes = JOB_CADENCE["odds_capture"].total_seconds() / 60
        assert finest <= capture_minutes, (finest, capture_minutes)

    def test_budget_report_is_generated_not_hardcoded(self) -> None:
        report = budget_report()
        assert report["tiers"], report
        assert len(report["tiers"]) == len(scheduler_cadence_tiers())
